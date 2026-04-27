import json
import html
import re
import sys
import time
import uuid
from pathlib import Path
from starlette.requests import Request
from starlette.responses import JSONResponse

# 支持直接运行：添加 src 目录到 Python 路径
src_dir = Path(__file__).parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastmcp import FastMCP, Context
from typing import Annotated, Optional
from pydantic import Field

# 尝试使用绝对导入（支持 mcp run）
try:
    from grok_search.providers.grok import GrokSearchProvider
    from grok_search.logger import log_exception, log_info
    from grok_search.config import config
    from grok_search.sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from grok_search.planning import engine as planning_engine, _split_csv
    from grok_search.utils import extract_unique_urls
except ImportError:
    from .providers.grok import GrokSearchProvider
    from .logger import log_exception, log_info
    from .config import config
    from .sources import SourcesCache, merge_sources, new_session_id, split_answer_and_sources
    from .planning import engine as planning_engine, _split_csv
    from .utils import extract_unique_urls

import asyncio

_SOURCES_CACHE = SourcesCache(max_size=256)
_AVAILABLE_MODELS_CACHE: dict[tuple[str, str], list[str]] = {}
_AVAILABLE_MODELS_LOCK = asyncio.Lock()
_URL_CLEAN_RE = re.compile(r"https?://[^\s<>\"']+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s+")


class CompatHTTPError(Exception):
    def __init__(self, status_code: int, message: str, payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {"detail": {"error": message}}


def _build_auth_provider():
    if not config.mcp_bearer_token:
        return None

    try:
        from fastmcp.server.auth import StaticTokenVerifier
    except ImportError:
        return None

    return StaticTokenVerifier(
        tokens={
            config.mcp_bearer_token: {
                "client_id": "grok-search-client",
                "scopes": ["mcp"],
            }
        }
    )


def build_mcp() -> FastMCP:
    server = FastMCP("grok-search", auth=_build_auth_provider())

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "transport": config.mcp_transport,
                "streamable_http_path": config.mcp_streamable_http_path,
                "sse_path": config.mcp_sse_path,
                "tavily_compatible_paths": ["/search", "/extract", "/crawl"],
                "tavily_compatible_auth_enabled": bool(config.mcp_bearer_token),
                "auth_enabled": bool(config.mcp_bearer_token),
                "debug_enabled": config.debug_enabled,
                "log_level": config.log_level,
                "log_dir": str(config.log_dir),
            }
        )

    return server


mcp = build_mcp()


async def _fetch_available_models(api_url: str, api_key: str) -> list[str]:
    import httpx

    models_url = f"{api_url.rstrip('/')}/models"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            models_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    models: list[str] = []
    for item in (data or {}).get("data", []) or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            models.append(item["id"])
    return models


async def _get_available_models_cached(api_url: str, api_key: str) -> list[str]:
    key = (api_url, api_key)
    async with _AVAILABLE_MODELS_LOCK:
        if key in _AVAILABLE_MODELS_CACHE:
            return _AVAILABLE_MODELS_CACHE[key]

    try:
        models = await _fetch_available_models(api_url, api_key)
    except Exception:
        models = []

    async with _AVAILABLE_MODELS_LOCK:
        _AVAILABLE_MODELS_CACHE[key] = models
    return models


def _extra_results_to_sources(
    tavily_results: list[dict] | None,
    firecrawl_results: list[dict] | None,
) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()

    if firecrawl_results:
        for r in firecrawl_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "firecrawl"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            desc = (r.get("description") or "").strip()
            if desc:
                item["description"] = desc
            sources.append(item)

    if tavily_results:
        for r in tavily_results:
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item: dict = {"url": url, "provider": "tavily"}
            title = (r.get("title") or "").strip()
            if title:
                item["title"] = title
            content = (r.get("content") or "").strip()
            if content:
                item["description"] = content
            sources.append(item)

    return sources


def _json_response(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status_code)


def _json_error(message: str, status_code: int = 400, error_type: str = "bad_request") -> JSONResponse:
    return _json_response({"detail": {"error": message}}, status_code=status_code)


async def _read_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON body: {e.msg}") from e
    except Exception as e:
        raise ValueError(f"Invalid JSON body: {e}") from e

    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def _extract_request_token(request: Request, body: dict) -> str | None:
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    for header in ("x-api-key", "api-key"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value

    for key in ("api_key", "apikey", "token"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _compat_auth_error(request: Request, body: dict) -> JSONResponse | None:
    expected = config.mcp_bearer_token
    if not expected:
        return None

    token = _extract_request_token(request, body)
    if token == expected:
        return None
    return _json_error("Unauthorized: missing or invalid API key.", status_code=401, error_type="unauthorized")


def _as_str(value, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_int(value, default: int, minimum: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_float(value, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_domain_list(value) -> list[str]:
    from urllib.parse import urlparse

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in _as_str_list(value):
        domain = raw.strip().lower()
        if not domain:
            continue
        if "://" in domain:
            domain = urlparse(domain).hostname or ""
        else:
            domain = domain.split("/", 1)[0]
        domain = domain.strip().strip(".")
        if domain.startswith("*."):
            domain = domain[2:]
        if not domain or domain in seen:
            continue
        seen.add(domain)
        normalized.append(domain)
    return normalized


def _domain_allowed(url: str, include_domains: list[str], exclude_domains: list[str]) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False

    def _matches(domain: str) -> bool:
        normalized = domain.lower().strip()
        if not normalized:
            return False
        return host == normalized or host.endswith(f".{normalized}")

    if include_domains and not any(_matches(domain) for domain in include_domains):
        return False
    if exclude_domains and any(_matches(domain) for domain in exclude_domains):
        return False
    return True


def _build_domain_search_query(
    query: str,
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_domain: str | None = None,
) -> str:
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []
    terms: list[str] = []
    if include_domain:
        terms.append(f"site:{include_domain}")
    elif include_domains:
        if len(include_domains) == 1:
            terms.append(f"site:{include_domains[0]}")
        else:
            terms.append("(" + " OR ".join(f"site:{domain}" for domain in include_domains) + ")")
    terms.extend(f"-site:{domain}" for domain in exclude_domains)
    terms.append(query)
    return " ".join(item for item in terms if item).strip()


def _build_grok_search_query(query: str, include_domains: list[str], exclude_domains: list[str]) -> str:
    constrained = _build_domain_search_query(
        query,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )
    if not include_domains and not exclude_domains:
        return constrained

    constraints: list[str] = []
    if include_domains:
        constraints.append("Only cite and return sources from these domains: " + ", ".join(include_domains) + ".")
    if exclude_domains:
        constraints.append("Do not cite or return sources from these domains: " + ", ".join(exclude_domains) + ".")
    return constrained + "\n\nSearch constraints: " + " ".join(constraints)


def _build_firecrawl_search_query(query: str, include_domain: str | None = None) -> str:
    if not include_domain:
        return query.strip()

    # Keep Firecrawl as a single-pass parallel branch. If the upstream returns
    # empty for site: queries, Tavily/Grok can still provide results.
    return f"site:{include_domain} {query}".strip()


def _compat_search_fetch_limit(max_results: int, has_domain_filters: bool, provider: str) -> int:
    if max_results <= 0:
        return 0
    if not has_domain_filters:
        return max_results
    if provider == "firecrawl":
        return min(50, max(max_results, max_results * 5))
    return min(20, max(max_results, max_results * 4))


def _dedupe_search_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        url = _clean_proxy_url(item.get("url"))
        if not url:
            continue
        key = _result_key(url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _source_to_tavily_result(source: dict, index: int, include_raw_content: bool = False) -> dict:
    url = _as_str(source.get("url")).strip()
    title = _as_str(source.get("title") or source.get("name") or url).strip() or url
    content = _as_str(
        source.get("content")
        or source.get("description")
        or source.get("snippet")
        or source.get("extract")
        or source.get("extracts")
    ).strip()
    result = {
        "title": title,
        "url": url,
        "content": content,
        "score": max(0.0, round(1.0 - index * 0.01, 4)),
    }
    if include_raw_content:
        result["raw_content"] = _as_str(source.get("raw_content") or content)
    return result


def _parse_map_urls(payload: str, seed_url: str, limit: int) -> tuple[str, list[str]]:
    base_url = seed_url
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        clean = (url or "").strip()
        if not clean.startswith(("http://", "https://")) or clean in seen:
            return
        seen.add(clean)
        urls.append(clean)

    _add(seed_url)

    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return base_url, urls[:limit]

    if isinstance(data, dict):
        base_url = _as_str(data.get("base_url") or seed_url).strip() or seed_url
        items = data.get("results") or data.get("urls") or []
    else:
        items = data

    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                _add(item)
            elif isinstance(item, dict):
                _add(_as_str(item.get("url") or item.get("href") or item.get("link")))
            if len(urls) >= limit:
                break

    return base_url, urls[:limit]


def _tavily_map_payload_error(payload: str) -> str:
    stripped = _as_str(payload).strip()
    if not stripped:
        return "Tavily map returned empty response"
    if stripped.startswith(("配置错误:", "映射超时:", "HTTP错误:", "映射错误:")):
        return stripped
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return "Tavily map returned non-JSON response"
    return ""


def _title_from_markdown(content: str, fallback: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:200]
    return fallback


def _new_request_id() -> str:
    return str(uuid.uuid4())


def _should_include_answer(value) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"basic", "advanced"}:
            return True
    return _as_bool(value, default=False)


def _normalize_raw_content_mode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "markdown" if value else None
    normalized = _as_str(value).strip().lower()
    if normalized in {"", "false", "0", "off", "no"}:
        return None
    if normalized in {"true", "1", "yes", "on", "markdown"}:
        return "markdown"
    if normalized == "text":
        return "text"
    raise ValueError("Invalid include_raw_content. Must be boolean, 'markdown', or 'text'.")


def _strip_compat_auth_fields(body: dict) -> dict:
    sanitized = dict(body)
    for key in ("api_key", "apikey", "token"):
        sanitized.pop(key, None)
    return sanitized


def _extract_error_message(payload: dict | None, fallback: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            message = detail.get("error")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("error")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


def _clean_proxy_url(value) -> str:
    raw = html.unescape(_as_str(value)).strip()
    if not raw:
        return ""

    match = _URL_CLEAN_RE.search(raw)
    if not match:
        return ""

    return match.group(0).rstrip(".,;:!?)")


def _clean_proxy_text(value, *, collapse_whitespace: bool = True) -> str:
    text = html.unescape(_as_str(value)).replace("\x00", " ").strip()
    if not text:
        return ""

    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace('">', " ").replace("'>", " ")
    if collapse_whitespace:
        text = _MULTISPACE_RE.sub(" ", text)
    return text.strip()


def _coerce_number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_top_images(items, *, include_descriptions: bool) -> list:
    normalized: list = []
    if not isinstance(items, list):
        return normalized

    for item in items:
        if isinstance(item, str):
            url = _clean_proxy_url(item)
            if url:
                normalized.append(url if not include_descriptions else {"url": url, "description": ""})
            continue

        if not isinstance(item, dict):
            continue

        url = _clean_proxy_url(item.get("url"))
        if not url:
            continue

        if include_descriptions:
            normalized.append(
                {
                    "url": url,
                    "description": _clean_proxy_text(item.get("description"), collapse_whitespace=True),
                }
            )
        else:
            normalized.append(url)

    return normalized


def _normalize_result_images(items, *, include_descriptions: bool) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(items, list):
        return normalized

    for item in items:
        if isinstance(item, str):
            url = _clean_proxy_url(item)
            if url:
                entry = {"url": url}
                if include_descriptions:
                    entry["description"] = ""
                normalized.append(entry)
            continue

        if not isinstance(item, dict):
            continue

        url = _clean_proxy_url(item.get("url"))
        if not url:
            continue

        entry = {"url": url}
        if include_descriptions:
            entry["description"] = _clean_proxy_text(item.get("description"), collapse_whitespace=True)
        normalized.append(entry)

    return normalized


def _normalize_tavily_search_payload(payload: dict, request_body: dict, elapsed: float) -> dict:
    include_raw_content = _normalize_raw_content_mode(request_body.get("include_raw_content"))
    include_favicon = _as_bool(request_body.get("include_favicon"), default=False)
    include_images = _as_bool(request_body.get("include_images"), default=False)
    include_image_descriptions = _as_bool(request_body.get("include_image_descriptions"), default=False)
    include_usage = _as_bool(request_body.get("include_usage"), default=False)
    include_answer = _should_include_answer(request_body.get("include_answer"))

    seen_urls: set[str] = set()
    results: list[dict] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue

        url = _clean_proxy_url(item.get("url"))
        if not url or url in seen_urls:
            continue

        title = _clean_proxy_text(item.get("title"), collapse_whitespace=True) or url
        content = _clean_proxy_text(item.get("content"), collapse_whitespace=True)
        result = {
            "title": title,
            "url": url,
            "content": content,
            "score": _coerce_number(item.get("score"), 0.0),
        }

        if include_raw_content:
            raw_content = item.get("raw_content")
            if isinstance(raw_content, str) and raw_content.strip():
                result["raw_content"] = html.unescape(raw_content).strip()

        if include_favicon:
            favicon = _clean_proxy_url(item.get("favicon"))
            if favicon:
                result["favicon"] = favicon

        if include_images:
            images = _normalize_result_images(item.get("images"), include_descriptions=include_image_descriptions)
            if images:
                result["images"] = images

        seen_urls.add(url)
        results.append(result)

    if not results:
        raise RuntimeError("Tavily upstream returned no usable search results")

    normalized = {
        "query": _as_str(payload.get("query") or request_body.get("query")).strip(),
        "answer": payload.get("answer") if include_answer else None,
        "images": _normalize_top_images(payload.get("images"), include_descriptions=include_image_descriptions) if include_images else [],
        "results": results,
        "response_time": round(_coerce_number(payload.get("response_time"), elapsed) or elapsed, 3),
        "request_id": _as_str(payload.get("request_id")).strip() or _new_request_id(),
    }

    if include_answer and isinstance(normalized["answer"], str):
        normalized["answer"] = _clean_proxy_text(normalized["answer"], collapse_whitespace=False)

    auto_parameters = payload.get("auto_parameters")
    if isinstance(auto_parameters, dict):
        normalized["auto_parameters"] = auto_parameters

    usage = payload.get("usage")
    if include_usage and isinstance(usage, dict):
        normalized["usage"] = usage

    return normalized


def _normalize_tavily_extract_payload(payload: dict, request_body: dict, elapsed: float) -> dict:
    include_images = _as_bool(request_body.get("include_images"), default=False)
    include_favicon = _as_bool(request_body.get("include_favicon"), default=False)
    include_usage = _as_bool(request_body.get("include_usage"), default=False)

    results: list[dict] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _clean_proxy_url(item.get("url"))
        if not url:
            continue
        entry = {"url": url, "raw_content": html.unescape(_as_str(item.get("raw_content"))).strip()}
        if include_images:
            images = _normalize_top_images(item.get("images"), include_descriptions=False)
            if images:
                entry["images"] = images
        if include_favicon:
            favicon = _clean_proxy_url(item.get("favicon"))
            if favicon:
                entry["favicon"] = favicon
        results.append(entry)

    failed_results: list[dict] = []
    for item in payload.get("failed_results") or []:
        if not isinstance(item, dict):
            continue
        url = _clean_proxy_url(item.get("url"))
        error = _clean_proxy_text(item.get("error"), collapse_whitespace=True)
        if url:
            failed_results.append({"url": url, "error": error or "Unknown error"})

    normalized = {
        "results": results,
        "failed_results": failed_results,
        "response_time": round(_coerce_number(payload.get("response_time"), elapsed) or elapsed, 3),
        "request_id": _as_str(payload.get("request_id")).strip() or _new_request_id(),
    }

    usage = payload.get("usage")
    if include_usage and isinstance(usage, dict):
        normalized["usage"] = usage

    return normalized


def _normalize_tavily_crawl_payload(payload: dict, request_body: dict, elapsed: float) -> dict:
    include_favicon = _as_bool(request_body.get("include_favicon"), default=False)
    include_usage = _as_bool(request_body.get("include_usage"), default=False)

    results: list[dict] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _clean_proxy_url(item.get("url"))
        if not url:
            continue
        entry = {"url": url, "raw_content": html.unescape(_as_str(item.get("raw_content"))).strip()}
        if include_favicon:
            favicon = _clean_proxy_url(item.get("favicon"))
            if favicon:
                entry["favicon"] = favicon
        results.append(entry)

    if not results:
        raise RuntimeError("Tavily upstream returned no usable crawl results")

    normalized = {
        "base_url": _clean_proxy_url(payload.get("base_url") or request_body.get("url")) or _as_str(request_body.get("url")).strip(),
        "results": results,
        "response_time": round(_coerce_number(payload.get("response_time"), elapsed) or elapsed, 3),
        "request_id": _as_str(payload.get("request_id")).strip() or _new_request_id(),
    }

    usage = payload.get("usage")
    if include_usage and isinstance(usage, dict):
        normalized["usage"] = usage

    return normalized


def _normalize_tavily_proxy_payload(path: str, payload: dict, request_body: dict, elapsed: float) -> dict:
    if path == "/search":
        return _normalize_tavily_search_payload(payload, request_body, elapsed)
    if path == "/extract":
        return _normalize_tavily_extract_payload(payload, request_body, elapsed)
    if path == "/crawl":
        return _normalize_tavily_crawl_payload(payload, request_body, elapsed)
    return payload


async def _proxy_tavily_post(path: str, body: dict, timeout: float) -> dict:
    import httpx

    if not config.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured")

    endpoint = f"{config.tavily_api_url.rstrip('/')}{path}"
    headers = {
        "Authorization": f"Bearer {config.tavily_api_key}",
        "Content-Type": "application/json",
    }
    sanitized_body = _strip_compat_auth_fields(body)
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, headers=headers, json=sanitized_body)
    except Exception as e:
        raise RuntimeError(f"Tavily upstream request failed: {e}") from e

    payload: dict | None
    try:
        parsed = response.json()
        payload = parsed if isinstance(parsed, dict) else {"data": parsed}
    except ValueError:
        payload = None

    if response.status_code >= 400:
        message = _extract_error_message(payload, response.text[:500] or f"[{response.status_code}] upstream error")
        raise CompatHTTPError(response.status_code, message, payload or {"detail": {"error": message}})

    if payload is None:
        raise RuntimeError("Tavily upstream returned a non-JSON response")

    elapsed = time.perf_counter() - started
    return _normalize_tavily_proxy_payload(path, payload, sanitized_body, elapsed)


def _result_text_from_source(source: dict) -> str:
    return _as_str(
        source.get("content")
        or source.get("description")
        or source.get("snippet")
        or source.get("extract")
        or source.get("extracts")
    ).strip()


def _compat_score(index: int) -> float:
    return max(0.0, round(1.0 - index * 0.01, 4))


def _search_branch_meta(provider: str, status: str, *, count: int = 0, reason: str = "") -> dict:
    meta = {
        "provider": provider,
        "status": status,
        "count": count,
        "elapsed_ms": 0,
        "kept": 0,
        "selected": 0,
        "filtered": 0,
        "deduped": 0,
    }
    clean_reason = _clean_proxy_text(reason, collapse_whitespace=True)
    if clean_reason:
        meta["reason"] = clean_reason
    return meta


def _elapsed_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _compat_channel_meta(
    provider: str,
    status: str,
    *,
    operation: str = "",
    count: int = 0,
    elapsed_ms: int = 0,
    reason: str = "",
) -> dict:
    meta = {
        "provider": provider,
        "status": status,
        "count": count,
        "elapsed_ms": elapsed_ms,
    }
    if operation:
        meta["operation"] = operation
    clean_reason = _clean_proxy_text(reason, collapse_whitespace=True)
    if clean_reason:
        meta["reason"] = clean_reason[:500]
    return meta


def _compat_provider_summary(item: dict) -> str:
    label = _as_str(item.get("provider")).strip()
    operation = _as_str(item.get("operation")).strip()
    if operation:
        label = f"{label}.{operation}" if label else operation

    parts = [
        f"{label}={item.get('status', 'unknown')}",
        f"count={item.get('count', 0)}",
        f"elapsed_ms={item.get('elapsed_ms', 0)}",
    ]
    for key in ("attempts", "success", "failed", "empty", "unavailable", "selected"):
        if key in item:
            parts.append(f"{key}={item.get(key, 0)}")
    if item.get("reason"):
        parts.append(f"reason={item['reason']}")
    return " ".join(parts)


def _format_compat_sources_summary(items: list[dict]) -> str:
    return ", ".join(_compat_provider_summary(item) for item in items) or "no channel stats"


def _fetch_overall_status(channel_statuses: list[str]) -> str:
    if not channel_statuses:
        return "skipped"
    if all(status == "unavailable" for status in channel_statuses):
        return "unavailable"
    if any(status == "failed" for status in channel_statuses):
        return "failed"
    if any(status == "empty" for status in channel_statuses):
        return "empty"
    return channel_statuses[-1]


def _aggregate_fetch_channel_stats(fetch_metas: list[dict]) -> list[dict]:
    provider_order = {"tavily": 0, "firecrawl": 1}
    operation_order = {"extract": 0, "scrape": 1}
    stats: dict[tuple[str, str], dict] = {}

    for fetch_meta in fetch_metas:
        for channel in fetch_meta.get("channels") or []:
            provider = _as_str(channel.get("provider")).strip()
            operation = _as_str(channel.get("operation")).strip()
            if not provider:
                continue
            key = (provider, operation)
            item = stats.setdefault(
                key,
                {
                    "provider": provider,
                    "operation": operation,
                    "status": "skipped",
                    "count": 0,
                    "attempts": 0,
                    "elapsed_ms": 0,
                    "success": 0,
                    "failed": 0,
                    "empty": 0,
                    "unavailable": 0,
                },
            )
            status = _as_str(channel.get("status") or "unknown").strip()
            item["attempts"] += 1
            item["elapsed_ms"] += _as_int(channel.get("elapsed_ms"), default=0, minimum=0, maximum=10_000_000)
            item["count"] += _as_int(channel.get("count"), default=0, minimum=0, maximum=10_000_000)
            if status in ("success", "failed", "empty", "unavailable"):
                item[status] += 1
            if channel.get("reason") and not item.get("reason"):
                item["reason"] = channel["reason"]

    aggregated = list(stats.values())
    for item in aggregated:
        if item["success"]:
            item["status"] = "success"
        elif item["failed"]:
            item["status"] = "failed"
        elif item["empty"]:
            item["status"] = "empty"
        elif item["unavailable"]:
            item["status"] = "unavailable"

    aggregated.sort(
        key=lambda item: (
            provider_order.get(item["provider"], 99),
            operation_order.get(item.get("operation", ""), 99),
        )
    )
    return aggregated


def _format_fetch_route_summary(fetch_metas: list[dict], *, limit: int = 8) -> str:
    routes: list[str] = []
    for meta in fetch_metas[:limit]:
        url = _clean_proxy_url(meta.get("url")) or _as_str(meta.get("url")).strip()
        provider = _as_str(meta.get("provider")).strip() or "none"
        status = _as_str(meta.get("status") or "unknown").strip()
        routes.append(f"{url}=>{provider}:{status} elapsed_ms={meta.get('elapsed_ms', 0)}")
    if len(fetch_metas) > limit:
        routes.append(f"...+{len(fetch_metas) - limit} more")
    return "; ".join(routes)


_SEARCH_PROVIDER_WEIGHTS = {"grok": 1.08, "tavily": 1.0, "firecrawl": 0.92}
_SEARCH_PROVIDER_PRIORITY = {"grok": 0, "tavily": 1, "firecrawl": 2}
_SEARCH_CONFIRMATION_WEIGHTS = {1: 1.0, 2: 1.25, 3: 1.45}


def _compute_search_source_targets(total: int, available_providers: set[str], *, has_domain_filters: bool = False) -> dict[str, int]:
    targets = {"grok": 0, "tavily": 0, "firecrawl": 0}
    if total <= 0 or not available_providers:
        return targets

    for provider in available_providers:
        targets[provider] = _compat_search_fetch_limit(total, has_domain_filters, provider)

    return targets


def _rank_based_score(index: int, total: int, *, top: float = 0.95, floor: float = 0.35) -> float:
    if total <= 1:
        return top
    step = (top - floor) / max(total - 1, 1)
    return max(floor, top - index * step)


def _normalized_search_score(value, fallback: float) -> float:
    score = _coerce_number(value, fallback)
    if score > 1.0 and score <= 100.0:
        score = score / 100.0
    return max(0.0, min(1.0, score))


def _result_key(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return url.rstrip("/")
    scheme = (parsed.scheme or "https").lower()
    path = parsed.path.rstrip("/") or "/"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    query = urlencode(query_items, doseq=True)
    return urlunparse((scheme, host, path, "", query, ""))


def _best_result_provider(provider_scores: dict[str, float]) -> str:
    if not provider_scores:
        return ""
    return max(
        provider_scores,
        key=lambda provider: (
            provider_scores[provider] * _SEARCH_PROVIDER_WEIGHTS.get(provider, 1.0),
            -_SEARCH_PROVIDER_PRIORITY.get(provider, 99),
        ),
    )


def _apply_result_confidence(item: dict) -> None:
    provider_scores = item.get("_provider_scores") or {}
    if not provider_scores:
        item["_confidence_raw"] = 0.0
        item["score"] = 0.0
        return

    provider_count = len(provider_scores)
    weighted_average = sum(
        score * _SEARCH_PROVIDER_WEIGHTS.get(provider, 1.0)
        for provider, score in provider_scores.items()
    ) / provider_count
    confirmation_weight = _SEARCH_CONFIRMATION_WEIGHTS.get(provider_count, _SEARCH_CONFIRMATION_WEIGHTS[3])
    confidence_raw = weighted_average * confirmation_weight
    item["_confidence_raw"] = confidence_raw
    item["score"] = round(min(1.0, confidence_raw), 6)


async def _build_grok_provider(model: str = "") -> GrokSearchProvider:
    api_url = config.grok_api_url
    api_key = config.grok_api_key
    effective_model = config.grok_model

    if model:
        available = await _get_available_models_cached(api_url, api_key)
        if available and model not in available:
            raise ValueError(f"无效模型: {model}")
        effective_model = model

    return GrokSearchProvider(api_url, api_key, effective_model)


async def _compat_fetch_url(
    url: str,
    *,
    query: str = "",
    chunks_per_source: int = 3,
    extract_depth: str = "basic",
    format_mode: str = "markdown",
    timeout: float | None = None,
) -> tuple[str, str, str]:
    content, provider, reason, _ = await _compat_fetch_url_with_meta(
        url,
        query=query,
        chunks_per_source=chunks_per_source,
        extract_depth=extract_depth,
        format_mode=format_mode,
        timeout=timeout,
    )
    return content, provider, reason


async def _compat_fetch_url_with_meta(
    url: str,
    *,
    query: str = "",
    chunks_per_source: int = 3,
    extract_depth: str = "basic",
    format_mode: str = "markdown",
    timeout: float | None = None,
) -> tuple[str, str, str, dict]:
    started = time.perf_counter()
    channels: list[dict] = []

    tavily_started = time.perf_counter()
    tavily_result = await _call_tavily_extract(
        url,
        query=query,
        chunks_per_source=chunks_per_source,
        extract_depth=extract_depth,
        format_mode=format_mode,
        timeout=timeout,
    )
    tavily_status = _as_str(tavily_result.get("status") or "unknown").strip()
    channels.append(
        _compat_channel_meta(
            "tavily",
            tavily_status,
            operation="extract",
            count=1 if tavily_status == "success" else 0,
            elapsed_ms=_elapsed_ms(tavily_started),
            reason=_as_str(tavily_result.get("reason")),
        )
    )
    if tavily_result["status"] == "success":
        return tavily_result["content"], "tavily", "", {
            "url": url,
            "provider": "tavily",
            "status": "success",
            "elapsed_ms": _elapsed_ms(started),
            "channels": channels,
        }

    firecrawl_started = time.perf_counter()
    firecrawl_result = await _call_firecrawl_scrape(url)
    firecrawl_status = _as_str(firecrawl_result.get("status") or "unknown").strip()
    channels.append(
        _compat_channel_meta(
            "firecrawl",
            firecrawl_status,
            operation="scrape",
            count=1 if firecrawl_status == "success" else 0,
            elapsed_ms=_elapsed_ms(firecrawl_started),
            reason=_as_str(firecrawl_result.get("reason")),
        )
    )
    if firecrawl_result["status"] == "success":
        return firecrawl_result["content"], "firecrawl", "", {
            "url": url,
            "provider": "firecrawl",
            "status": "success",
            "elapsed_ms": _elapsed_ms(started),
            "channels": channels,
        }

    reasons = [
        _as_str(tavily_result.get("reason")).strip(),
        _as_str(firecrawl_result.get("reason")).strip(),
    ]
    reason = " | ".join([item for item in reasons if item]) or "No content returned"
    return "", "", reason, {
        "url": url,
        "provider": "",
        "status": _fetch_overall_status([tavily_status, firecrawl_status]),
        "elapsed_ms": _elapsed_ms(started),
        "reason": _clean_proxy_text(reason, collapse_whitespace=True),
        "channels": channels,
    }


async def _compat_search_payload(body: dict) -> dict:
    query = _as_str(body.get("query")).strip()
    if not query:
        raise ValueError("Missing required field: query")

    max_results = _as_int(body.get("max_results") or body.get("limit"), default=5, minimum=0, maximum=20)
    include_answer = _should_include_answer(body.get("include_answer"))
    raw_content_mode = _normalize_raw_content_mode(body.get("include_raw_content"))
    include_favicon = _as_bool(body.get("include_favicon"), default=False)
    include_images = _as_bool(body.get("include_images"), default=False)
    include_image_descriptions = _as_bool(body.get("include_image_descriptions"), default=False)
    include_domains = _normalize_domain_list(body.get("include_domains"))
    exclude_domains = _normalize_domain_list(body.get("exclude_domains"))
    include_usage = _as_bool(body.get("include_usage"), default=False)
    chunks_per_source = _as_int(body.get("chunks_per_source"), default=3, minimum=1, maximum=3)
    search_depth = _as_str(body.get("search_depth") or "basic").strip().lower() or "basic"
    platform = _as_str(body.get("platform") or body.get("topic")).strip()
    model = _as_str(body.get("model")).strip()

    started = time.perf_counter()
    request_id = _new_request_id()
    branch_meta: list[dict] = []
    branch_meta_by_provider: dict[str, dict] = {}
    available_providers: set[str] = set()
    try:
        config.grok_api_url
        config.grok_api_key
        available_providers.add("grok")
    except ValueError:
        pass
    if config.tavily_api_key:
        available_providers.add("tavily")
    if config.firecrawl_api_key:
        available_providers.add("firecrawl")
    has_domain_filters = bool(include_domains or exclude_domains)
    search_targets = _compute_search_source_targets(
        max_results,
        available_providers,
        has_domain_filters=has_domain_filters,
    )

    def _set_branch_meta(meta: dict) -> None:
        existing = branch_meta_by_provider.get(meta["provider"])
        if existing is None:
            branch_meta_by_provider[meta["provider"]] = meta
            branch_meta.append(meta)
            return
        existing.update(meta)

    def _finish_branch_meta(meta: dict, started_at: float) -> dict:
        meta["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000)
        meta["target"] = meta.get("planned", 0)
        _set_branch_meta(meta)
        return meta

    async def _safe_tavily_search() -> dict | None:
        branch_started = time.perf_counter()
        if not config.tavily_api_key:
            meta = _search_branch_meta("tavily", "disabled")
            meta["planned"] = 0
            _finish_branch_meta(meta, branch_started)
            return None
        if search_targets["tavily"] <= 0:
            meta = _search_branch_meta("tavily", "skipped")
            meta["planned"] = 0
            meta["reason"] = "planned fetch target is zero"
            _finish_branch_meta(meta, branch_started)
            return None
        tavily_body = dict(body)
        tavily_body["include_answer"] = False
        tavily_body["max_results"] = search_targets["tavily"]
        tavily_body["include_domains"] = include_domains
        tavily_body["exclude_domains"] = exclude_domains
        try:
            payload = await _proxy_tavily_post("/search", tavily_body, timeout=90.0)
            meta = _search_branch_meta("tavily", "success", count=len(payload.get("results") or []))
            meta["planned"] = search_targets["tavily"]
            _finish_branch_meta(meta, branch_started)
            return payload
        except Exception as e:
            meta = _search_branch_meta("tavily", "failed", reason=str(e))
            meta["planned"] = search_targets["tavily"]
            _finish_branch_meta(meta, branch_started)
            if config.debug_enabled:
                await log_exception(None, "Combined search Tavily branch failed", e, True)
            return None

    async def _safe_firecrawl_search() -> list[dict] | None:
        branch_started = time.perf_counter()
        if not config.firecrawl_api_key:
            meta = _search_branch_meta("firecrawl", "disabled")
            meta["planned"] = 0
            _finish_branch_meta(meta, branch_started)
            return None
        if search_targets["firecrawl"] <= 0:
            meta = _search_branch_meta("firecrawl", "skipped")
            meta["planned"] = 0
            meta["reason"] = "planned fetch target is zero"
            _finish_branch_meta(meta, branch_started)
            return None
        try:
            payload = await _call_firecrawl_search(
                query,
                search_targets["firecrawl"],
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            meta = _search_branch_meta("firecrawl", "success" if payload else "empty", count=len(payload or []))
            meta["planned"] = search_targets["firecrawl"]
            _finish_branch_meta(meta, branch_started)
            return payload
        except Exception as e:
            meta = _search_branch_meta("firecrawl", "failed", reason=str(e))
            meta["planned"] = search_targets["firecrawl"]
            _finish_branch_meta(meta, branch_started)
            if config.debug_enabled:
                await log_exception(None, "Combined search Firecrawl branch failed", e, True)
            return None

    async def _safe_grok_search() -> tuple[GrokSearchProvider | None, str]:
        branch_started = time.perf_counter()
        if "grok" not in available_providers:
            meta = _search_branch_meta("grok", "disabled")
            meta["planned"] = 0
            meta["reason"] = "GROK_API_URL or GROK_API_KEY is not configured"
            _finish_branch_meta(meta, branch_started)
            return None, ""
        if search_targets["grok"] <= 0:
            meta = _search_branch_meta("grok", "skipped")
            meta["planned"] = 0
            meta["reason"] = "planned fetch target is zero"
            _finish_branch_meta(meta, branch_started)
            return None, ""
        try:
            provider = await _build_grok_provider(model)
        except Exception as e:
            meta = _search_branch_meta("grok", "failed", reason=str(e))
            meta["planned"] = search_targets["grok"]
            _finish_branch_meta(meta, branch_started)
            if config.debug_enabled:
                await log_exception(None, "Combined search Grok provider build failed", e, True)
            return None, ""

        try:
            payload = await provider.search(_build_grok_search_query(query, include_domains, exclude_domains), platform, ctx=None)
            meta = _search_branch_meta("grok", "success" if payload.strip() else "empty")
            meta["planned"] = search_targets["grok"]
            _finish_branch_meta(meta, branch_started)
            return provider, payload
        except Exception as e:
            meta = _search_branch_meta("grok", "failed", reason=str(e))
            meta["planned"] = search_targets["grok"]
            _finish_branch_meta(meta, branch_started)
            if config.debug_enabled:
                await log_exception(None, "Combined search Grok branch failed", e, True)
            return provider, ""

    tavily_payload, firecrawl_results, grok_result = await asyncio.gather(
        _safe_tavily_search(),
        _safe_firecrawl_search(),
        _safe_grok_search(),
    )

    grok_provider, grok_raw = grok_result
    answer, grok_sources = split_answer_and_sources(grok_raw)
    grok_candidate_keys = {
        _result_key(url)
        for url in [
            *[_clean_proxy_url(source.get("url")) for source in grok_sources],
            *(extract_unique_urls(grok_raw) if grok_raw else []),
        ]
        if url
    }
    grok_candidate_count = len(grok_candidate_keys)
    if "grok" in branch_meta_by_provider:
        branch_meta_by_provider["grok"]["count"] = grok_candidate_count

    top_images = []
    auto_parameters = None
    usage = {"credits": 0} if include_usage else None
    if tavily_payload:
        top_images = tavily_payload.get("images", []) if include_images else []
        auto_parameters = tavily_payload.get("auto_parameters")
        if include_usage and isinstance(tavily_payload.get("usage"), dict):
            usage = tavily_payload["usage"]

    combined_by_url: dict[str, dict] = {}
    combined_order: list[str] = []

    def _merge_result(item: dict, provider: str, fallback_score: float) -> None:
        meta = branch_meta_by_provider.get(provider)
        if meta is None:
            meta = _search_branch_meta(provider, "success")
            _set_branch_meta(meta)

        url = _clean_proxy_url(item.get("url"))
        if not url:
            meta["filtered"] += 1
            return
        if not _domain_allowed(url, include_domains, exclude_domains):
            meta["filtered"] += 1
            return

        result_key = _result_key(url)
        title = _clean_proxy_text(item.get("title") or item.get("name") or url, collapse_whitespace=True) or url
        content = _clean_proxy_text(
            item.get("content")
            or item.get("description")
            or item.get("snippet")
            or item.get("extract")
            or item.get("extracts"),
            collapse_whitespace=True,
        )
        score = _normalized_search_score(item.get("score"), fallback_score)
        priority = _SEARCH_PROVIDER_PRIORITY.get(provider, 9)
        existing = combined_by_url.get(result_key)

        if existing is None:
            entry = {
                "title": title,
                "url": url,
                "content": content,
                "score": score,
                "source": provider,
                "_priority": priority,
                "_sources": [provider],
                "_provider_scores": {provider: score},
            }
            raw_content = item.get("raw_content")
            if raw_content_mode and isinstance(raw_content, str) and raw_content.strip():
                entry["raw_content"] = html.unescape(raw_content).strip()
            if include_favicon:
                favicon = _clean_proxy_url(item.get("favicon"))
                if favicon:
                    entry["favicon"] = favicon
            if include_images:
                images = _normalize_result_images(item.get("images"), include_descriptions=include_image_descriptions)
                if images:
                    entry["images"] = images

            combined_by_url[result_key] = entry
            combined_order.append(result_key)
            meta["kept"] += 1
            return

        meta["deduped"] += 1
        old_priority = existing["_priority"]
        existing["_priority"] = min(old_priority, priority)
        provider_scores = existing.setdefault("_provider_scores", {})
        provider_scores[provider] = max(provider_scores.get(provider, 0.0), score)
        if provider not in existing["_sources"]:
            existing["_sources"].append(provider)

        if title and (
            existing.get("title") == existing["url"]
            or (priority < old_priority)
        ):
            existing["title"] = title
            existing["source"] = provider
        if content and (
            not existing.get("content")
            or (priority < old_priority)
            or len(content) > len(existing.get("content", ""))
        ):
            existing["content"] = content
            if priority <= old_priority:
                existing["source"] = provider
        if raw_content_mode and "raw_content" not in existing:
            raw_content = item.get("raw_content")
            if isinstance(raw_content, str) and raw_content.strip():
                existing["raw_content"] = html.unescape(raw_content).strip()
        if include_favicon and "favicon" not in existing:
            favicon = _clean_proxy_url(item.get("favicon"))
            if favicon:
                existing["favicon"] = favicon
        if include_images and "images" not in existing:
            images = _normalize_result_images(item.get("images"), include_descriptions=include_image_descriptions)
            if images:
                existing["images"] = images

    for idx, item in enumerate((tavily_payload or {}).get("results") or []):
        _merge_result(item, "tavily", _compat_score(idx))

    for idx, item in enumerate(firecrawl_results or []):
        _merge_result(item, "firecrawl", max(0.0, 0.55 - idx * 0.01))

    for idx, source in enumerate(grok_sources):
        _merge_result(source, "grok", max(0.0, 0.4 - idx * 0.01))

    if grok_raw and max_results > 0:
        for url in extract_unique_urls(grok_raw):
            _merge_result({"url": url, "title": url, "content": ""}, "grok", 0.2)

    combined_results = [combined_by_url[url] for url in combined_order]
    for item in combined_results:
        _apply_result_confidence(item)
        provider_scores = item.get("_provider_scores") or {}
        best_provider = _best_result_provider(provider_scores)
        if best_provider:
            item["source"] = best_provider

    ranking_strategy = "confidence"
    if grok_provider and len(combined_results) > 1:
        sources_text = "\n\n".join(
            [
                f"{idx}. Title: {item['title']}\nURL: {item['url']}\n"
                f"Sources: {', '.join(item.get('_sources', []))}\n"
                f"Confidence: {item.get('score', 0)}\n"
                f"Snippet: {item.get('content', '')}"
                for idx, item in enumerate(combined_results, start=1)
            ]
        )
        try:
            ranking = await grok_provider.rank_sources(query, sources_text, len(combined_results))
            combined_results = [
                combined_results[idx - 1]
                for idx in ranking
                if 1 <= idx <= len(combined_results)
            ]
            ranking_strategy = "grok_rank_sources"
        except Exception as e:
            if config.debug_enabled:
                await log_exception(None, "Combined search ranking failed", e, True)
            combined_results.sort(key=lambda item: (-item["score"], item["_priority"]))
            ranking_strategy = "confidence_fallback"
    else:
        combined_results.sort(key=lambda item: (-item["score"], item["_priority"]))

    final_results = combined_results[:max_results] if max_results > 0 else []
    for item in final_results:
        for provider in item.get("_sources", []):
            meta = branch_meta_by_provider.get(provider)
            if meta:
                meta["selected"] += 1

    if grok_provider and final_results:
        to_describe = [item for item in final_results if not item.get("content")]
        if to_describe:
            semaphore = asyncio.Semaphore(4)

            async def _describe(item: dict) -> None:
                async with semaphore:
                    try:
                        described = await grok_provider.describe_url(item["url"])
                    except Exception:
                        return
                    title = _as_str(described.get("title")).strip()
                    extracts = _as_str(described.get("extracts")).strip()
                    if title:
                        item["title"] = title
                    if extracts:
                        item["content"] = extracts

            await asyncio.gather(*[_describe(item) for item in to_describe])

    if raw_content_mode and final_results:
        semaphore = asyncio.Semaphore(4)

        async def _attach_raw_content(item: dict) -> None:
            async with semaphore:
                if item.get("raw_content"):
                    return
                raw_content, _, _ = await _compat_fetch_url(
                    item["url"],
                    query=query,
                    chunks_per_source=chunks_per_source,
                    extract_depth="advanced" if search_depth == "advanced" else "basic",
                    format_mode=raw_content_mode,
                )
                if raw_content.strip():
                    item["raw_content"] = raw_content

        await asyncio.gather(*[_attach_raw_content(item) for item in final_results])

    for item in final_results:
        item.pop("_confidence_raw", None)
        item.pop("_provider_scores", None)
        item.pop("_priority", None)
        item["sources"] = item.pop("_sources", [item.get("source")] if item.get("source") else [])

    summary = ", ".join(
        [
            f"{item['provider']}={item['status']}"
            + (f"({item['count']})" if item.get("count") is not None else "")
            + f" elapsed_ms={item.get('elapsed_ms', 0)}"
            + f" target={item.get('target', item.get('planned', search_targets.get(item['provider'], 0)))}"
            + f" kept={item.get('kept', 0)} selected={item.get('selected', 0)}"
            + f" filtered={item.get('filtered', 0)} deduped={item.get('deduped', 0)}"
            + (f": {item['reason']}" if item.get("reason") else "")
            for item in branch_meta
        ]
    )
    await log_info(
        None,
        f"Combined search branches for query={query!r}: ranking={ranking_strategy}; {summary}; final_results={len(final_results)}",
        True,
    )

    if max_results > 0 and not final_results:
        raise RuntimeError("Search returned no structured results")

    payload = {
        "query": query,
        "answer": _clean_proxy_text(answer, collapse_whitespace=False) if include_answer and answer else None,
        "images": top_images if include_images else [],
        "results": final_results,
        "response_time": round(time.perf_counter() - started, 3),
        "request_id": request_id,
    }
    payload["search_sources"] = branch_meta
    payload["search_ranking"] = ranking_strategy
    if isinstance(auto_parameters, dict):
        payload["auto_parameters"] = auto_parameters
    if include_usage:
        payload["usage"] = usage or {"credits": 0}
    return payload


async def _compat_extract_payload(body: dict) -> dict:
    timeout = _as_float(body.get("timeout"), default=60.0, minimum=1.0, maximum=60.0)
    urls_value = body.get("urls")
    urls = _as_str_list(urls_value)
    single_url = _as_str(body.get("url")).strip()
    if single_url:
        urls.insert(0, single_url)
    if not urls:
        raise ValueError("Missing required field: urls")
    urls = list(dict.fromkeys(urls))
    if len(urls) > 20:
        raise ValueError("Max 20 URLs are allowed.")

    query = _as_str(body.get("query")).strip()
    chunks_per_source = _as_int(body.get("chunks_per_source"), default=3, minimum=1, maximum=5)
    extract_depth = _as_str(body.get("extract_depth") or "basic").strip().lower() or "basic"
    format_mode = _as_str(body.get("format") or "markdown").strip().lower() or "markdown"
    include_images = _as_bool(body.get("include_images"), default=False)
    include_favicon = _as_bool(body.get("include_favicon"), default=False)
    include_usage = _as_bool(body.get("include_usage"), default=False)

    started = time.perf_counter()
    request_id = _new_request_id()
    results: list[dict] = []
    failed_results: list[dict] = []
    fetch_metas: list[dict] = []

    for url in urls:
        content, provider, reason, fetch_meta = await _compat_fetch_url_with_meta(
            url,
            query=query,
            chunks_per_source=chunks_per_source,
            extract_depth=extract_depth,
            format_mode=format_mode,
            timeout=timeout,
        )
        fetch_metas.append(fetch_meta)
        if content.strip():
            item = {"url": url, "raw_content": content}
            if provider:
                item["source"] = provider
            if include_images:
                item["images"] = []
            if include_favicon:
                favicon = ""
                if favicon:
                    item["favicon"] = favicon
            results.append(item)
        else:
            failed_results.append({"url": url, "error": reason or "No content returned"})

    extract_sources = _aggregate_fetch_channel_stats(fetch_metas)
    elapsed = time.perf_counter() - started
    await log_info(
        None,
        (
            f"Compatible extract: urls={len(urls)} success={len(results)} failed={len(failed_results)} "
            f"elapsed_ms={round(elapsed * 1000)}; channels={_format_compat_sources_summary(extract_sources)}; "
            f"routes={_format_fetch_route_summary(fetch_metas)}"
        ),
        True,
    )

    payload = {
        "results": results,
        "failed_results": failed_results,
        "response_time": round(elapsed, 3),
        "request_id": request_id,
        "extract_sources": extract_sources,
    }
    if include_usage:
        payload["usage"] = {"credits": 0}
    return payload


async def _compat_crawl_payload(body: dict) -> dict:
    timeout = _as_float(body.get("timeout"), default=150.0, minimum=10.0, maximum=150.0)
    fallback_reason = ""
    started = time.perf_counter()
    request_id = _new_request_id()
    crawl_attempt_meta = None
    if config.tavily_api_key:
        tavily_crawl_started = time.perf_counter()
        try:
            payload = await _proxy_tavily_post("/crawl", body, timeout=timeout + 10.0)
            elapsed = time.perf_counter() - started
            crawl_sources = [
                _compat_channel_meta(
                    "tavily",
                    "success",
                    operation="crawl",
                    count=len(payload.get("results") or []),
                    elapsed_ms=_elapsed_ms(tavily_crawl_started),
                )
            ]
            payload["response_time"] = round(elapsed, 3)
            payload["request_id"] = _as_str(payload.get("request_id")).strip() or request_id
            payload["crawl_sources"] = crawl_sources
            await log_info(
                None,
                (
                    f"Compatible crawl: mode=tavily status=success url={_as_str(body.get('url')).strip()!r} "
                    f"results={len(payload.get('results') or [])} elapsed_ms={round(elapsed * 1000)}; "
                    f"channels={_format_compat_sources_summary(crawl_sources)}"
                ),
                True,
            )
            return payload
        except CompatHTTPError as e:
            fallback_reason = str(e)
            crawl_attempt_meta = _compat_channel_meta(
                "tavily",
                "failed",
                operation="crawl",
                count=0,
                elapsed_ms=_elapsed_ms(tavily_crawl_started),
                reason=f"[{e.status_code}] {fallback_reason}",
            )
            await log_info(
                None,
                (
                    "Tavily crawl failed, falling back to local crawl: "
                    f"elapsed_ms={crawl_attempt_meta['elapsed_ms']} status_code={e.status_code} reason={fallback_reason}"
                ),
                True,
            )
        except Exception as e:
            fallback_reason = str(e)
            crawl_attempt_meta = _compat_channel_meta(
                "tavily",
                "failed",
                operation="crawl",
                count=0,
                elapsed_ms=_elapsed_ms(tavily_crawl_started),
                reason=fallback_reason,
            )
            await log_info(
                None,
                (
                    "Tavily crawl failed, falling back to local crawl: "
                    f"elapsed_ms={crawl_attempt_meta['elapsed_ms']} reason={fallback_reason}"
                ),
                True,
            )
            if config.debug_enabled:
                await log_exception(None, "Tavily upstream crawl proxy failed, falling back to local compatibility path", e, True)
    else:
        crawl_attempt_meta = _compat_channel_meta(
            "tavily",
            "unavailable",
            operation="crawl",
            count=0,
            elapsed_ms=0,
            reason="TAVILY_API_KEY is not configured",
        )

    url = _as_str(body.get("url")).strip()
    if not url:
        raise ValueError("Missing required field: url")

    limit = _as_int(body.get("limit"), default=50, minimum=1, maximum=500)
    max_depth = _as_int(body.get("max_depth"), default=1, minimum=1, maximum=5)
    max_breadth = _as_int(body.get("max_breadth"), default=20, minimum=1, maximum=500)
    instructions = _as_str(body.get("instructions")).strip()
    chunks_per_source = _as_int(body.get("chunks_per_source"), default=3, minimum=1, maximum=5)
    extract_depth = _as_str(body.get("extract_depth") or "basic").strip().lower() or "basic"
    format_mode = _as_str(body.get("format") or "markdown").strip().lower() or "markdown"
    include_usage = _as_bool(body.get("include_usage"), default=False)

    base_url = url
    urls = [url]
    map_meta = _compat_channel_meta(
        "tavily",
        "skipped",
        operation="map",
        count=0,
        elapsed_ms=0,
        reason="limit <= 1 or TAVILY_API_KEY is not configured",
    )
    if config.tavily_api_key and limit > 1:
        map_started = time.perf_counter()
        try:
            map_payload = await _call_tavily_map(
                url,
                instructions=instructions,
                max_depth=max_depth,
                max_breadth=max_breadth,
                limit=limit,
                timeout=timeout,
            )
            map_elapsed_ms = _elapsed_ms(map_started)
            map_error = _tavily_map_payload_error(map_payload)
            if map_error:
                map_meta = _compat_channel_meta(
                    "tavily",
                    "failed",
                    operation="map",
                    count=0,
                    elapsed_ms=map_elapsed_ms,
                    reason=map_error,
                )
                fallback_reason = " | ".join([item for item in (fallback_reason, map_error) if item])
                await log_info(
                    None,
                    f"Tavily map failed during crawl fallback, scraping seed URL only: elapsed_ms={map_elapsed_ms} reason={map_error}",
                    True,
                )
            else:
                base_url, urls = _parse_map_urls(map_payload, url, limit)
                map_meta = _compat_channel_meta(
                    "tavily",
                    "success" if urls else "empty",
                    operation="map",
                    count=len(urls),
                    elapsed_ms=map_elapsed_ms,
                    reason="" if urls else "Tavily map returned no URLs",
                )
                if not urls:
                    urls = [url]
        except Exception as e:
            reason = str(e)
            fallback_reason = " | ".join([item for item in (fallback_reason, reason) if item])
            map_meta = _compat_channel_meta(
                "tavily",
                "failed",
                operation="map",
                count=0,
                elapsed_ms=_elapsed_ms(map_started),
                reason=reason,
            )
            await log_info(
                None,
                (
                    "Tavily map failed during crawl fallback, scraping seed URL only: "
                    f"elapsed_ms={map_meta['elapsed_ms']} reason={reason}"
                ),
                True,
            )
            if config.debug_enabled:
                await log_exception(None, "Tavily map failed during crawl fallback", e, True)

    semaphore = asyncio.Semaphore(3)

    async def _fetch_for_crawl(target_url: str) -> tuple[str, str, str, str, dict]:
        async with semaphore:
            content, provider, reason, fetch_meta = await _compat_fetch_url_with_meta(
                target_url,
                query=instructions,
                chunks_per_source=chunks_per_source,
                extract_depth=extract_depth,
                format_mode=format_mode,
                timeout=timeout,
            )
            return target_url, content, provider, reason, fetch_meta

    fetched = await asyncio.gather(*[_fetch_for_crawl(item) for item in urls[:limit]])
    fetch_metas = [item[4] for item in fetched]

    results: list[dict] = []
    for target_url, content, provider, reason, fetch_meta in fetched:
        if not content.strip():
            continue

        item = {"url": target_url, "raw_content": content}
        if provider:
            item["source"] = provider
        if provider == "tavily":
            favicon = ""
        else:
            favicon = ""
        if favicon:
            item["favicon"] = favicon
        results.append(item)

    if not results:
        crawl_sources = [item for item in (crawl_attempt_meta, map_meta) if item is not None]
        crawl_sources.extend(_aggregate_fetch_channel_stats(fetch_metas))
        elapsed = time.perf_counter() - started
        await log_info(
            None,
            (
                f"Compatible crawl: mode=local status=failed url={url!r} discovered={len(urls)} results=0 "
                f"elapsed_ms={round(elapsed * 1000)}; channels={_format_compat_sources_summary(crawl_sources)}; "
                f"routes={_format_fetch_route_summary(fetch_metas)}"
            ),
            True,
        )
        raise RuntimeError("Crawl returned no results")

    crawl_sources = [item for item in (crawl_attempt_meta, map_meta) if item is not None]
    crawl_sources.extend(_aggregate_fetch_channel_stats(fetch_metas))
    elapsed = time.perf_counter() - started
    for source in crawl_sources:
        if source.get("provider") in {"tavily", "firecrawl"} and source.get("operation") in {"extract", "scrape"}:
            source["selected"] = sum(1 for item in results if item.get("source") == source["provider"])

    payload = {
        "base_url": base_url,
        "results": results,
        "response_time": round(elapsed, 3),
        "request_id": request_id,
        "crawl_sources": crawl_sources,
    }
    if include_usage:
        payload["usage"] = {"credits": 0}
    if fallback_reason:
        payload["fallback"] = {
            "from": "tavily",
            "to": "local",
            "reason": _clean_proxy_text(fallback_reason, collapse_whitespace=True),
        }
    await log_info(
        None,
        (
            f"Compatible crawl: mode=local status=success url={url!r} discovered={len(urls)} "
            f"results={len(results)} elapsed_ms={round(elapsed * 1000)}; "
            f"channels={_format_compat_sources_summary(crawl_sources)}; routes={_format_fetch_route_summary(fetch_metas)}"
        ),
        True,
    )
    return payload


@mcp.custom_route("/search", methods=["POST"], include_in_schema=False)
async def tavily_compatible_search(request: Request) -> JSONResponse:
    try:
        body = await _read_json_body(request)
        auth_error = _compat_auth_error(request, body)
        if auth_error:
            return auth_error
        return _json_response(await _compat_search_payload(body))
    except CompatHTTPError as e:
        return _json_response(e.payload, status_code=e.status_code)
    except ValueError as e:
        return _json_error(str(e), status_code=400)
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, "Tavily-compatible search failed", e, True)
        return _json_error(str(e), status_code=500, error_type="internal_error")


@mcp.custom_route("/extract", methods=["POST"], include_in_schema=False)
async def tavily_compatible_extract(request: Request) -> JSONResponse:
    try:
        body = await _read_json_body(request)
        auth_error = _compat_auth_error(request, body)
        if auth_error:
            return auth_error
        return _json_response(await _compat_extract_payload(body))
    except CompatHTTPError as e:
        return _json_response(e.payload, status_code=e.status_code)
    except ValueError as e:
        return _json_error(str(e), status_code=400)
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, "Tavily-compatible extract failed", e, True)
        return _json_error(str(e), status_code=500, error_type="internal_error")


@mcp.custom_route("/crawl", methods=["POST"], include_in_schema=False)
async def tavily_compatible_crawl(request: Request) -> JSONResponse:
    try:
        body = await _read_json_body(request)
        auth_error = _compat_auth_error(request, body)
        if auth_error:
            return auth_error
        return _json_response(await _compat_crawl_payload(body))
    except CompatHTTPError as e:
        return _json_response(e.payload, status_code=e.status_code)
    except ValueError as e:
        return _json_error(str(e), status_code=400)
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, "Tavily-compatible crawl failed", e, True)
        return _json_error(str(e), status_code=500, error_type="internal_error")


@mcp.tool(
    name="web_search",
    output_schema=None,
    description="""
    Before using this tool, please use the plan_intent tool to plan the search carefully.
    Performs a deep web search based on the given query and returns Grok's answer directly.

    This tool extracts sources if provided by upstream, caches them, and returns:
    - session_id: string (When you feel confused or curious about the main content, use this field to invoke the get_sources tool to obtain the corresponding list of information sources)
    - content: string (answer only)
    - sources_count: int
    """,
    meta={"version": "2.0.0", "author": "guda.studio"},
)
async def web_search(
    query: Annotated[str, "Clear, self-contained natural-language search query."],
    platform: Annotated[str, "Target platform to focus on (e.g., 'Twitter', 'GitHub', 'Reddit'). Leave empty for general web search."] = "",
    model: Annotated[str, "Optional model ID for this request only. This value is used ONLY when user explicitly provided."] = "",
    extra_sources: Annotated[int, "Number of additional reference results from Tavily/Firecrawl. Set 0 to disable. Default 0."] = 0,
    ctx: Context = None,
) -> dict:
    session_id = new_session_id()
    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key
    except ValueError as e:
        await _SOURCES_CACHE.set(session_id, [])
        return {"session_id": session_id, "content": f"配置错误: {str(e)}", "sources_count": 0}

    effective_model = config.grok_model
    if model:
        available = await _get_available_models_cached(api_url, api_key)
        if available and model not in available:
            await _SOURCES_CACHE.set(session_id, [])
            return {"session_id": session_id, "content": f"无效模型: {model}", "sources_count": 0}
        effective_model = model

    grok_provider = GrokSearchProvider(api_url, api_key, effective_model)

    # 计算额外信源配额
    has_tavily = bool(config.tavily_api_key)
    has_firecrawl = bool(config.firecrawl_api_key)
    firecrawl_count = 0
    tavily_count = 0
    if extra_sources > 0:
        if has_firecrawl and has_tavily:
            firecrawl_count = round(extra_sources * 1)
            tavily_count = extra_sources - firecrawl_count
        elif has_firecrawl:
            firecrawl_count = extra_sources
        elif has_tavily:
            tavily_count = extra_sources

    # 并行执行搜索任务
    async def _safe_grok() -> str:
        try:
            return await grok_provider.search(query, platform, ctx=ctx)
        except Exception as e:
            await log_exception(ctx, "Grok search failed", e, config.debug_enabled)
            return ""

    async def _safe_tavily() -> list[dict] | None:
        try:
            if tavily_count:
                return await _call_tavily_search(query, tavily_count)
        except Exception as e:
            await log_exception(ctx, "Tavily extra search failed", e, config.debug_enabled)
            return None

    async def _safe_firecrawl() -> list[dict] | None:
        try:
            if firecrawl_count:
                return await _call_firecrawl_search(query, firecrawl_count)
        except Exception as e:
            await log_exception(ctx, "Firecrawl extra search failed", e, config.debug_enabled)
            return None

    coros: list = [_safe_grok()]
    if tavily_count > 0:
        coros.append(_safe_tavily())
    if firecrawl_count > 0:
        coros.append(_safe_firecrawl())

    gathered = await asyncio.gather(*coros)

    grok_result: str = gathered[0] or ""
    tavily_results: list[dict] | None = None
    firecrawl_results: list[dict] | None = None
    idx = 1
    if tavily_count > 0:
        tavily_results = gathered[idx]
        idx += 1
    if firecrawl_count > 0:
        firecrawl_results = gathered[idx]

    answer, grok_sources = split_answer_and_sources(grok_result)
    extra = _extra_results_to_sources(tavily_results, firecrawl_results)
    all_sources = merge_sources(grok_sources, extra)

    if not answer and not all_sources:
        await _SOURCES_CACHE.set(session_id, [])
        if not config.grok_api_key:
            return {"session_id": session_id, "content": "配置错误: GROK_API_KEY 未配置", "sources_count": 0}
        if extra_sources > 0 and (tavily_results or firecrawl_results):
            return {"session_id": session_id, "content": "搜索失败: Grok 未返回答案，但补充信源已不可用或被过滤", "sources_count": 0}
        return {"session_id": session_id, "content": "搜索失败: Grok 未返回答案且没有可用信源", "sources_count": 0}

    await _SOURCES_CACHE.set(session_id, all_sources)
    return {"session_id": session_id, "content": answer, "sources_count": len(all_sources)}


@mcp.tool(
    name="get_sources",
    description="""
    When you feel confused or curious about the search response content, use the session_id returned by web_search to invoke the this tool to obtain the corresponding list of information sources.
    Retrieve all cached sources for a previous web_search call.
    Provide the session_id returned by web_search to get the full source list.
    """,
    meta={"version": "1.0.0", "author": "guda.studio"},
)
async def get_sources(
    session_id: Annotated[str, "Session ID from previous web_search call."]
) -> dict:
    sources = await _SOURCES_CACHE.get(session_id)
    if sources is None:
        return {
            "session_id": session_id,
            "sources": [],
            "sources_count": 0,
            "error": "session_id_not_found_or_expired",
        }
    return {"session_id": session_id, "sources": sources, "sources_count": len(sources)}


def _fetch_result(status: str, *, content: str = "", reason: str = "") -> dict[str, str]:
    return {
        "status": status,
        "content": content,
        "reason": reason,
    }


async def _call_tavily_extract(
    url: str,
    *,
    query: str = "",
    chunks_per_source: int = 3,
    extract_depth: str = "basic",
    format_mode: str = "markdown",
    timeout: float | None = None,
) -> dict[str, str]:
    import httpx
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return _fetch_result("unavailable", reason="TAVILY_API_KEY 未配置")
    endpoint = f"{api_url.rstrip('/')}/extract"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "urls": [url],
        "extract_depth": extract_depth,
        "format": format_mode,
    }
    if query:
        body["query"] = query
        body["chunks_per_source"] = chunks_per_source
    request_timeout = timeout if timeout is not None else (30.0 if extract_depth == "advanced" else 10.0)
    try:
        async with httpx.AsyncClient(timeout=request_timeout + 10.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            if data.get("results") and len(data["results"]) > 0:
                content = data["results"][0].get("raw_content", "")
                if content and content.strip():
                    return _fetch_result("success", content=content)
                return _fetch_result("empty", reason="Tavily 返回空内容")
            return _fetch_result("empty", reason="Tavily 未返回 results")
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, f"Tavily extract failed for {url}", e, True)
        return _fetch_result("failed", reason=str(e))


async def _call_tavily_search(query: str, max_results: int = 6) -> list[dict] | None:
    import httpx
    api_key = config.tavily_api_key
    if not api_key:
        return None
    endpoint = f"{config.tavily_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", ""), "score": r.get("score", 0)}
                for r in results
            ] if results else None
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, f"Tavily search failed for query={query!r}", e, True)
        return None


async def _call_firecrawl_search(
    query: str,
    limit: int = 14,
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[dict] | None:
    import httpx
    api_key = config.firecrawl_api_key
    if not api_key:
        return None
    endpoint = f"{config.firecrawl_api_url.rstrip('/')}/search"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _extract_results(data: dict) -> list[dict] | None:
        results = data.get("data", {}).get("web", [])
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "description": r.get("description", "")}
            for r in results
        ] if results else None

    async def _request_once(client: httpx.AsyncClient, search_query: str) -> list[dict] | None:
        body = {"query": search_query, "limit": limit}
        response = await client.post(endpoint, headers=headers, json=body)
        response.raise_for_status()
        return _extract_results(response.json())

    async def _request_domain(client: httpx.AsyncClient, domain: str) -> list[dict]:
        results = await _request_once(client, _build_firecrawl_search_query(query, include_domain=domain))
        return [
            item for item in results or []
            if _domain_allowed(_clean_proxy_url(item.get("url")), [domain], exclude_domains or [])
        ]

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            if include_domains:
                per_domain_results = await asyncio.gather(
                    *[_request_domain(client, domain) for domain in include_domains]
                )
                merged: list[dict] = []
                for results in per_domain_results:
                    merged.extend(results or [])
                return _dedupe_search_items(merged)[:limit] or None

            results = await _request_once(client, _build_firecrawl_search_query(query))
            if exclude_domains and results:
                results = [
                    item for item in results
                    if _domain_allowed(_clean_proxy_url(item.get("url")), [], exclude_domains)
                ]
            return _dedupe_search_items(results or [])[:limit] or None
    except Exception as e:
        if config.debug_enabled:
            await log_exception(None, f"Firecrawl search failed for query={query!r}", e, True)
        return None


async def _call_firecrawl_scrape(url: str, ctx=None) -> dict[str, str]:
    import httpx
    api_url = config.firecrawl_api_url
    api_key = config.firecrawl_api_key
    if not api_key:
        return _fetch_result("unavailable", reason="FIRECRAWL_API_KEY 未配置")
    endpoint = f"{api_url.rstrip('/')}/scrape"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_retries = config.retry_max_attempts
    for attempt in range(max_retries):
        body = {
            "url": url,
            "formats": ["markdown"],
            "timeout": 60000,
            "waitFor": (attempt + 1) * 1500,
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
                markdown = data.get("data", {}).get("markdown", "")
                if markdown and markdown.strip():
                    return _fetch_result("success", content=markdown)
                await log_info(ctx, f"Firecrawl: markdown为空, 重试 {attempt + 1}/{max_retries}", config.debug_enabled)
        except Exception as e:
            await log_info(ctx, f"Firecrawl error: {e}", config.debug_enabled)
            return _fetch_result("failed", reason=str(e))
    return _fetch_result("empty", reason="Firecrawl 返回空内容")


@mcp.tool(
    name="web_fetch",
    output_schema=None,
    description="""
    Fetches and extracts complete content from a URL, returning it as a structured Markdown document.

    **Key Features:**
        - **Full Content Extraction:** Retrieves and parses all meaningful content (text, images, links, tables, code blocks).
        - **Markdown Conversion:** Converts HTML structure to well-formatted Markdown with preserved hierarchy.
        - **Content Fidelity:** Maintains 100% content fidelity without summarization or modification.

    **Edge Cases & Best Practices:**
        - Ensure URL is complete and accessible (not behind authentication or paywalls).
        - May not capture dynamically loaded content requiring JavaScript execution.
        - Large pages may take longer to process; consider timeout implications.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def web_fetch(
    url: Annotated[str, "Valid HTTP/HTTPS web address pointing to the target page. Must be complete and accessible."],
    ctx: Context = None
) -> str:
    await log_info(ctx, f"Begin Fetch: {url}", config.debug_enabled)

    tavily_result = await _call_tavily_extract(url)
    if tavily_result["status"] == "success":
        await log_info(ctx, "Fetch Finished (Tavily)!", config.debug_enabled)
        return tavily_result["content"]

    if tavily_result["status"] == "unavailable":
        await log_info(ctx, f"Tavily unavailable, trying Firecrawl... ({tavily_result['reason']})", config.debug_enabled)
    elif tavily_result["status"] == "failed":
        await log_info(ctx, f"Tavily failed, trying Firecrawl... ({tavily_result['reason']})", config.debug_enabled)
    else:
        await log_info(ctx, f"Tavily returned no usable content, trying Firecrawl... ({tavily_result['reason']})", config.debug_enabled)

    firecrawl_result = await _call_firecrawl_scrape(url, ctx)
    if firecrawl_result["status"] == "success":
        await log_info(ctx, "Fetch Finished (Firecrawl)!", config.debug_enabled)
        return firecrawl_result["content"]

    if firecrawl_result["status"] == "unavailable":
        await log_info(ctx, f"Fetch Failed: Firecrawl unavailable ({firecrawl_result['reason']})", config.debug_enabled)
    elif firecrawl_result["status"] == "failed":
        await log_info(ctx, f"Fetch Failed: Firecrawl failed ({firecrawl_result['reason']})", config.debug_enabled)
    else:
        await log_info(ctx, f"Fetch Failed: Firecrawl returned no usable content ({firecrawl_result['reason']})", config.debug_enabled)

    if not config.tavily_api_key and not config.firecrawl_api_key:
        return "配置错误: TAVILY_API_KEY 和 FIRECRAWL_API_KEY 均未配置"
    return "提取失败: 所有提取服务均未能获取内容"


async def _call_tavily_map(url: str, instructions: str = None, max_depth: int = 1,
                           max_breadth: int = 20, limit: int = 50, timeout: int = 150) -> str:
    import httpx
    import json
    api_url = config.tavily_api_url
    api_key = config.tavily_api_key
    if not api_key:
        return "配置错误: TAVILY_API_KEY 未配置，请设置环境变量 TAVILY_API_KEY"
    endpoint = f"{api_url.rstrip('/')}/map"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"url": url, "max_depth": max_depth, "max_breadth": max_breadth, "limit": limit, "timeout": timeout}
    if instructions:
        body["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=float(timeout + 10)) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            return json.dumps({
                "base_url": data.get("base_url", ""),
                "results": data.get("results", []),
                "response_time": data.get("response_time", 0)
            }, ensure_ascii=False, indent=2)
    except httpx.TimeoutException:
        return f"映射超时: 请求超过{timeout}秒"
    except httpx.HTTPStatusError as e:
        return f"HTTP错误: {e.response.status_code} - {e.response.text[:200]}"
    except Exception as e:
        return f"映射错误: {str(e)}"


@mcp.tool(
    name="web_map",
    description="""
    Maps a website's structure by traversing it like a graph, discovering URLs and generating a comprehensive site map.

    **Key Features:**
        - **Graph Traversal:** Explores website structure starting from root URL.
        - **Depth & Breadth Control:** Configure traversal limits to balance coverage and performance.
        - **Instruction Filtering:** Use natural language to focus crawler on specific content types.

    **Edge Cases & Best Practices:**
        - Start with low max_depth (1-2) for initial exploration, increase if needed.
        - Use instructions to filter for specific content (e.g., "only documentation pages").
        - Large sites may hit timeout limits; adjust timeout and limit parameters accordingly.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def web_map(
    url: Annotated[str, "Root URL to begin the mapping (e.g., 'https://docs.example.com')."],
    instructions: Annotated[str, "Natural language instructions for the crawler to filter or focus on specific content."] = "",
    max_depth: Annotated[int, Field(description="Maximum depth of mapping from the base URL.", ge=1, le=5)] = 1,
    max_breadth: Annotated[int, Field(description="Maximum number of links to follow per page.", ge=1, le=500)] = 20,
    limit: Annotated[int, Field(description="Total number of links to process before stopping.", ge=1, le=500)] = 50,
    timeout: Annotated[int, Field(description="Maximum time in seconds for the operation.", ge=10, le=150)] = 150
) -> str:
    result = await _call_tavily_map(url, instructions, max_depth, max_breadth, limit, timeout)
    return result


@mcp.tool(
    name="get_config_info",
    output_schema=None,
    description="""
    Returns current Grok Search MCP server configuration and tests API connectivity.

    **Key Features:**
        - **Configuration Check:** Verifies environment variables and current settings.
        - **Connection Test:** Sends request to /models endpoint to validate API access.
        - **Model Discovery:** Lists all available models from the API.

    **Edge Cases & Best Practices:**
        - Use this tool first when debugging connection or configuration issues.
        - API keys are automatically masked for security in the response.
        - Connection test timeout is 10 seconds; network issues may cause delays.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def get_config_info() -> str:
    import json
    import httpx

    config_info = config.get_config_info()

    # 添加连接测试
    test_result = {
        "status": "未测试",
        "message": "",
        "response_time_ms": 0
    }

    try:
        api_url = config.grok_api_url
        api_key = config.grok_api_key

        # 构建 /models 端点 URL
        models_url = f"{api_url.rstrip('/')}/models"

        # 发送测试请求
        import time
        start_time = time.time()

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                models_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
            )

            response_time = (time.time() - start_time) * 1000  # 转换为毫秒

            if response.status_code == 200:
                test_result["status"] = "✅ 连接成功"
                test_result["message"] = f"成功获取模型列表 (HTTP {response.status_code})"
                test_result["response_time_ms"] = round(response_time, 2)

                # 尝试解析返回的模型列表
                try:
                    models_data = response.json()
                    if "data" in models_data and isinstance(models_data["data"], list):
                        model_count = len(models_data["data"])
                        test_result["message"] += f"，共 {model_count} 个模型"

                        # 提取所有模型的 ID/名称
                        model_names = []
                        for model in models_data["data"]:
                            if isinstance(model, dict) and "id" in model:
                                model_names.append(model["id"])

                        if model_names:
                            test_result["available_models"] = model_names
                except:
                    pass
            else:
                test_result["status"] = "⚠️ 连接异常"
                test_result["message"] = f"HTTP {response.status_code}: {response.text[:100]}"
                test_result["response_time_ms"] = round(response_time, 2)

    except httpx.TimeoutException:
        test_result["status"] = "❌ 连接超时"
        test_result["message"] = "请求超时（10秒），请检查网络连接或 API URL"
    except httpx.RequestError as e:
        test_result["status"] = "❌ 连接失败"
        test_result["message"] = f"网络错误: {str(e)}"
    except ValueError as e:
        test_result["status"] = "❌ 配置错误"
        test_result["message"] = str(e)
    except Exception as e:
        test_result["status"] = "❌ 测试失败"
        test_result["message"] = f"未知错误: {str(e)}"

    config_info["connection_test"] = test_result

    return json.dumps(config_info, ensure_ascii=False, indent=2)


@mcp.tool(
    name="switch_model",
    output_schema=None,
    description="""
    Switches the default Grok model used for search and fetch operations, persisting the setting.

    **Key Features:**
        - **Model Selection:** Change the AI model for web search and content fetching.
        - **Persistent Storage:** Model preference saved to ~/.config/grok-search/config.json.
        - **Immediate Effect:** New model used for all subsequent operations.

    **Edge Cases & Best Practices:**
        - Use get_config_info to verify available models before switching.
        - Invalid model IDs may cause API errors in subsequent requests.
        - Model changes persist across sessions until explicitly changed again.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def switch_model(
    model: Annotated[str, "Model ID to switch to (e.g., 'grok-4-fast', 'grok-2-latest', 'grok-vision-beta')."]
) -> str:
    import json

    try:
        previous_model = config.grok_model
        config.set_model(model)
        current_model = config.grok_model

        result = {
            "status": "✅ 成功",
            "previous_model": previous_model,
            "current_model": current_model,
            "message": f"模型已从 {previous_model} 切换到 {current_model}",
            "config_file": str(config.config_file)
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except ValueError as e:
        result = {
            "status": "❌ 失败",
            "message": f"切换模型失败: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        result = {
            "status": "❌ 失败",
            "message": f"未知错误: {str(e)}"
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="toggle_builtin_tools",
    output_schema=None,
    description="""
    Toggle Claude Code's built-in WebSearch and WebFetch tools on/off.

    **Key Features:**
        - **Tool Control:** Enable or disable Claude Code's native web tools.
        - **Project Scope:** Changes apply to current project's .claude/settings.json.
        - **Status Check:** Query current state without making changes.

    **Edge Cases & Best Practices:**
        - Use "on" to block built-in tools when preferring this MCP server's implementation.
        - Use "off" to restore Claude Code's native tools.
        - Use "status" to check current configuration without modification.
    """,
    meta={"version": "1.3.0", "author": "guda.studio"},
)
async def toggle_builtin_tools(
    action: Annotated[str, "Action to perform: 'on' (block built-in), 'off' (allow built-in), or 'status' (check current state)."] = "status"
) -> str:
    import json

    # Locate project root
    root = Path.cwd()
    while root != root.parent and not (root / ".git").exists():
        root = root.parent

    settings_path = root / ".claude" / "settings.json"
    tools = ["WebFetch", "WebSearch"]

    # Load or initialize
    if settings_path.exists():
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    else:
        settings = {"permissions": {"deny": []}}

    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    blocked = all(t in deny for t in tools)

    # Execute action
    if action in ["on", "enable"]:
        for t in tools:
            if t not in deny:
                deny.append(t)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已禁用"
        blocked = True
    elif action in ["off", "disable"]:
        deny[:] = [t for t in deny if t not in tools]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        msg = "官方工具已启用"
        blocked = False
    else:
        msg = f"官方工具当前{'已禁用' if blocked else '已启用'}"

    return json.dumps({
        "blocked": blocked,
        "deny_list": deny,
        "file": str(settings_path),
        "message": msg
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_intent",
    output_schema=None,
    description="""
    Phase 1 of search planning: Analyze user intent. Call this FIRST to create a session.
    Returns session_id for subsequent phases. Required flow:
    plan_intent → plan_complexity → plan_sub_query(×N) → plan_search_term(×N) → plan_tool_mapping(×N) → plan_execution

    Required phases depend on complexity: Level 1 = phases 1-3; Level 2 = phases 1-5; Level 3 = all 6.
    """,
)
async def plan_intent(
    thought: Annotated[str, "Reasoning for this phase"],
    core_question: Annotated[str, "Distilled core question in one sentence"],
    query_type: Annotated[str, "factual | comparative | exploratory | analytical"],
    time_sensitivity: Annotated[str, "realtime | recent | historical | irrelevant"],
    session_id: Annotated[str, "Empty for new session, or existing ID to revise"] = "",
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    domain: Annotated[str, "Specific domain if identifiable"] = "",
    premise_valid: Annotated[Optional[bool], "False if the question contains a flawed assumption"] = None,
    ambiguities: Annotated[str, "Comma-separated unresolved ambiguities"] = "",
    unverified_terms: Annotated[str, "Comma-separated external terms to verify"] = "",
    is_revision: Annotated[bool, "True to overwrite existing intent"] = False,
) -> str:
    import json
    data = {"core_question": core_question, "query_type": query_type, "time_sensitivity": time_sensitivity}
    if domain:
        data["domain"] = domain
    if premise_valid is not None:
        data["premise_valid"] = premise_valid
    if ambiguities:
        data["ambiguities"] = _split_csv(ambiguities)
    if unverified_terms:
        data["unverified_terms"] = _split_csv(unverified_terms)
    return json.dumps(planning_engine.process_phase(
        phase="intent_analysis", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=data,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_complexity",
    output_schema=None,
    description="Phase 2: Assess search complexity (1-3). Controls required phases: Level 1 = phases 1-3; Level 2 = phases 1-5; Level 3 = all 6.",
)
async def plan_complexity(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for complexity assessment"],
    level: Annotated[int, "Complexity 1-3"],
    estimated_sub_queries: Annotated[int, "Expected number of sub-queries"],
    estimated_tool_calls: Annotated[int, "Expected total tool calls"],
    justification: Annotated[str, "Why this complexity level"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    is_revision: Annotated[bool, "True to overwrite"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    return json.dumps(planning_engine.process_phase(
        phase="complexity_assessment", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence,
        phase_data={"level": level, "estimated_sub_queries": estimated_sub_queries,
                     "estimated_tool_calls": estimated_tool_calls, "justification": justification},
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_sub_query",
    output_schema=None,
    description="Phase 3: Add one sub-query. Call once per sub-query; data accumulates across calls. Set is_revision=true to replace all.",
)
async def plan_sub_query(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this sub-query"],
    id: Annotated[str, "Unique ID (e.g., 'sq1')"],
    goal: Annotated[str, "Sub-query goal"],
    expected_output: Annotated[str, "What success looks like"],
    boundary: Annotated[str, "What this excludes — mutual exclusion with siblings"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    depends_on: Annotated[str, "Comma-separated prerequisite IDs"] = "",
    tool_hint: Annotated[str, "web_search | web_fetch | web_map"] = "",
    is_revision: Annotated[bool, "True to replace all sub-queries"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    item = {"id": id, "goal": goal, "expected_output": expected_output, "boundary": boundary}
    if depends_on:
        item["depends_on"] = _split_csv(depends_on)
    if tool_hint:
        item["tool_hint"] = tool_hint
    return json.dumps(planning_engine.process_phase(
        phase="query_decomposition", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=item,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_search_term",
    output_schema=None,
    description="Phase 4: Add one search term. Call once per term; data accumulates. First call must set approach.",
)
async def plan_search_term(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this search term"],
    term: Annotated[str, "Search query (max 8 words)"],
    purpose: Annotated[str, "Sub-query ID this serves (e.g., 'sq1')"],
    round: Annotated[int, "Execution round: 1=broad, 2+=targeted follow-up"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    approach: Annotated[str, "broad_first | narrow_first | targeted (required on first call)"] = "",
    fallback_plan: Annotated[str, "Fallback if primary searches fail"] = "",
    is_revision: Annotated[bool, "True to replace all search terms"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    data = {"search_terms": [{"term": term, "purpose": purpose, "round": round}]}
    if approach:
        data["approach"] = approach
    if fallback_plan:
        data["fallback_plan"] = fallback_plan
    return json.dumps(planning_engine.process_phase(
        phase="search_strategy", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=data,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_tool_mapping",
    output_schema=None,
    description="Phase 5: Map a sub-query to a tool. Call once per mapping; data accumulates.",
)
async def plan_tool_mapping(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for this mapping"],
    sub_query_id: Annotated[str, "Sub-query ID to map"],
    tool: Annotated[str, "web_search | web_fetch | web_map"],
    reason: Annotated[str, "Why this tool for this sub-query"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    params_json: Annotated[str, "Optional JSON string for tool-specific params"] = "",
    is_revision: Annotated[bool, "True to replace all mappings"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    item = {"sub_query_id": sub_query_id, "tool": tool, "reason": reason}
    if params_json:
        try:
            item["params"] = json.loads(params_json)
        except json.JSONDecodeError:
            pass
    return json.dumps(planning_engine.process_phase(
        phase="tool_selection", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence, phase_data=item,
    ), ensure_ascii=False, indent=2)


@mcp.tool(
    name="plan_execution",
    output_schema=None,
    description="Phase 6: Define execution order. parallel_groups: semicolon-separated groups of comma-separated IDs (e.g., 'sq1,sq2;sq3').",
)
async def plan_execution(
    session_id: Annotated[str, "Session ID from plan_intent"],
    thought: Annotated[str, "Reasoning for execution order"],
    parallel_groups: Annotated[str, "Parallel batches: 'sq1,sq2;sq3,sq4' (semicolon=groups, comma=IDs)"],
    sequential: Annotated[str, "Comma-separated IDs that must run in order"],
    estimated_rounds: Annotated[int, "Estimated execution rounds"],
    confidence: Annotated[float, "Confidence 0.0-1.0"] = 1.0,
    is_revision: Annotated[bool, "True to overwrite"] = False,
) -> str:
    import json
    if not planning_engine.get_session(session_id):
        return json.dumps({"error": f"Session '{session_id}' not found. Call plan_intent first."})
    parallel = [_split_csv(g) for g in parallel_groups.split(";") if g.strip()] if parallel_groups else []
    seq = _split_csv(sequential)
    return json.dumps(planning_engine.process_phase(
        phase="execution_order", thought=thought, session_id=session_id,
        is_revision=is_revision, confidence=confidence,
        phase_data={"parallel": parallel, "sequential": seq, "estimated_rounds": estimated_rounds},
    ), ensure_ascii=False, indent=2)


def main():
    import signal
    import os
    import threading

    # 信号处理（仅主线程）
    if threading.current_thread() is threading.main_thread():
        def handle_shutdown(signum, frame):
            os._exit(0)
        signal.signal(signal.SIGINT, handle_shutdown)
        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, handle_shutdown)

    # Windows 父进程监控
    if sys.platform == 'win32':
        import time
        import ctypes
        parent_pid = os.getppid()

        def is_parent_alive(pid):
            """Windows 下检查进程是否存活"""
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return True
            exit_code = ctypes.c_ulong()
            result = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return result and exit_code.value == STILL_ACTIVE

        def monitor_parent():
            while True:
                if not is_parent_alive(parent_pid):
                    os._exit(0)
                time.sleep(2)

        threading.Thread(target=monitor_parent, daemon=True).start()

    try:
        run_kwargs = {"transport": config.mcp_transport, "show_banner": False}
        if config.mcp_transport != "stdio":
            run_kwargs.update(
                {
                    "host": config.mcp_host,
                    "port": config.mcp_port,
                    "path": (
                        config.mcp_sse_path
                        if config.mcp_transport == "sse"
                        else config.mcp_streamable_http_path
                    ),
                    "stateless_http": config.mcp_stateless_http,
                }
            )
        mcp.run(**run_kwargs)
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
