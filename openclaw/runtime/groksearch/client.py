from __future__ import annotations

import json
import itertools
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from groksearch.config import GrokSearchConfig


class GrokSearchError(RuntimeError):
    """Raised when the bundled GrokSearch OpenClaw runtime fails."""


def _parse_sse_json(body: str) -> dict[str, Any]:
    last_payload: dict[str, Any] | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    last_payload = parsed
                    if "result" in parsed or "error" in parsed:
                        return parsed
    if last_payload is not None:
        return last_payload
    raise GrokSearchError("remote MCP response did not include SSE data payload")


def _decode_json_response(body: str) -> dict[str, Any]:
    if not body.strip():
        return {}
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise GrokSearchError("remote MCP response is not a JSON object")
    return parsed


class _McpHttpSession:
    def __init__(self, config: GrokSearchConfig) -> None:
        self.config = config
        self._id_counter = itertools.count(1)
        self.session_id = ""
        self.protocol_version = ""

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": self.config.http_user_agent,
        }
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.protocol_version:
            headers["mcp-protocol-version"] = self.protocol_version
        return headers

    def _post(self, payload: dict[str, Any], *, expect_response: bool = True) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.config.mcp_url,
            data=body,
            headers=self._base_headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.tool_timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                session_id = response.headers.get("mcp-session-id", "").strip()
                if session_id:
                    self.session_id = session_id
                if not expect_response:
                    return {}
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    return _parse_sse_json(response_body)
                return _decode_json_response(response_body)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GrokSearchError(f"remote MCP HTTP {exc.code}: {detail[:400]}") from exc
        except URLError as exc:
            raise GrokSearchError(f"request failed: {exc}") from exc

    def initialize(self) -> None:
        init_payload = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "groksearch-openclaw", "version": "0.2.0"},
            },
        }
        result = self._post(init_payload)
        init_result = (result.get("result") or {}) if isinstance(result, dict) else {}
        self.protocol_version = str(init_result.get("protocolVersion") or "2025-11-25")
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
        )

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": next(self._id_counter),
                "method": "tools/list",
                "params": {},
            }
        )
        payload = result.get("result") or {}
        tools = payload.get("tools") or []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._post(
            {
                "jsonrpc": "2.0",
                "id": next(self._id_counter),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                },
            }
        )
        if "error" in result:
            error = result["error"] or {}
            raise GrokSearchError(
                f"remote MCP error calling {name}: {error.get('message', 'unknown error')}"
            )
        payload = result.get("result")
        if not isinstance(payload, dict):
            raise GrokSearchError(f"remote MCP returned invalid tool result for {name}")
        if payload.get("isError"):
            message = ""
            for item in payload.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    message = str(item["text"])
                    break
            raise GrokSearchError(message or f"remote tool {name} returned an error")
        return payload


def _text_from_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(str(item["text"]))
    return "\n".join(parts).strip()


def _json_from_result(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    text = _text_from_result(result)
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise GrokSearchError("tool returned JSON that is not an object")
    return parsed


class GrokSearchClient:
    def __init__(self, config: GrokSearchConfig | None = None) -> None:
        self.config = config or GrokSearchConfig.from_env()

    def _session(self) -> _McpHttpSession:
        session = _McpHttpSession(self.config)
        session.initialize()
        return session

    def list_tools(self) -> list[str]:
        session = self._session()
        return [tool.get("name", "") for tool in session.list_tools() if tool.get("name")]

    def probe(self) -> dict[str, Any]:
        request = Request(
            self.config.mcp_url,
            headers=self.config.headers(accept_sse=True),
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.config.verify_timeout_seconds) as response:
                status = response.status
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise GrokSearchError(f"request failed: {exc}") from exc

        payload: dict[str, Any] = {
            "url": self.config.mcp_url,
            "status_code": status,
            "body": body,
            "user_agent": self.config.http_user_agent,
        }
        body_lower = body.lower()
        if status == 403 and "cloudflare" in body_lower and "1010" in body_lower:
            payload["note"] = (
                "remote endpoint is reachable but blocked by Cloudflare/WAF "
                "(error 1010). This is not a local OpenClaw install failure."
            )
        elif status == 403 and "cloudflare" in body_lower:
            payload["note"] = "remote endpoint is reachable but blocked by Cloudflare/WAF."
        payload["ok"] = status in {200, 400, 401, 406}
        return payload

    def health_http(self) -> dict[str, Any]:
        request = Request(
            self.config.health_url,
            headers=self.config.headers(),
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.config.verify_timeout_seconds) as response:
                status = response.status
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise GrokSearchError(f"request failed: {exc}") from exc

        return {
            "url": self.config.health_url,
            "status_code": status,
            "body": body,
            "ok": status == 200,
        }

    def health(self) -> dict[str, Any]:
        probe = self.probe()
        tools: list[str] | None = None
        tool_error = ""
        remote_config: dict[str, Any] | None = None
        try:
            tools = self.list_tools()
            remote_config = self.get_config_info()
        except Exception as exc:
            tool_error = str(exc)

        return {
            "runtime": self.config.describe(),
            "probe": probe,
            "tools": tools or [],
            "tool_error": tool_error,
            "remote_config": remote_config or {},
        }

    def get_config_info(self) -> dict[str, Any]:
        session = self._session()
        result = session.call_tool("get_config_info", {})
        return _json_from_result(result)

    @staticmethod
    def _looks_like_error_text(text: str) -> bool:
        lowered = text.lower()
        error_markers = (
            "配置错误",
            "提取失败",
            "映射错误",
            "映射超时",
            "http错误",
            "missing ",
            "not configured",
            "failed",
            "error:",
        )
        return any(marker in lowered for marker in error_markers)

    def search(
        self,
        *,
        query: str,
        platform: str = "",
        model: str = "",
        extra_sources: int = 0,
    ) -> dict[str, Any]:
        session = self._session()
        result = session.call_tool(
            "web_search",
            {
                "query": query,
                "platform": platform,
                "model": model,
                "extra_sources": extra_sources,
            },
        )
        payload = _json_from_result(result)
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            try:
                sources_result = session.call_tool("get_sources", {"session_id": session_id})
                sources = _json_from_result(sources_result)
            except Exception as exc:
                payload["sources_error"] = str(exc)
            else:
                payload["sources"] = sources.get("sources") or []
                payload["sources_count"] = sources.get("sources_count", payload.get("sources_count", 0))
        else:
            payload.setdefault("sources", [])
        return payload

    def get_sources(self, *, session_id: str) -> dict[str, Any]:
        session = self._session()
        result = session.call_tool("get_sources", {"session_id": session_id})
        return _json_from_result(result)

    def extract(self, *, url: str) -> dict[str, Any]:
        session = self._session()
        result = session.call_tool("web_fetch", {"url": url})
        content = _text_from_result(result)
        payload = {"url": url, "content": content, "provider": "groksearch-mcp"}
        if content and self._looks_like_error_text(content):
            payload["warning"] = content
        return payload

    def map(
        self,
        *,
        url: str,
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        timeout: int = 150,
    ) -> dict[str, Any]:
        session = self._session()
        result = session.call_tool(
            "web_map",
            {
                "url": url,
                "instructions": instructions,
                "max_depth": max_depth,
                "max_breadth": max_breadth,
                "limit": limit,
                "timeout": timeout,
            },
        )
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("result"), str):
            text = structured["result"]
        else:
            text = _text_from_result(result)
        if not text:
            return {"url": url, "results": []}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            payload = {"url": url, "raw": text}
            if text and self._looks_like_error_text(text):
                payload["warning"] = text
            return payload
        if isinstance(parsed, dict):
            parsed.setdefault("url", url)
            if text and self._looks_like_error_text(text):
                parsed.setdefault("warning", text)
            return parsed
        payload = {"url": url, "raw": text}
        if text and self._looks_like_error_text(text):
            payload["warning"] = text
        return payload

    def research(
        self,
        *,
        query: str,
        platform: str = "",
        extra_sources: int = 3,
        map_url: str = "",
        map_instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        timeout: int = 150,
    ) -> dict[str, Any]:
        search_payload = self.search(
            query=query,
            platform=platform,
            extra_sources=extra_sources,
        )
        source_items = [
            item for item in (search_payload.get("sources") or [])
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]
        candidate_urls: list[str] = []
        for item in source_items:
            source_url = str(item.get("url") or "").strip()
            if source_url and source_url not in candidate_urls:
                candidate_urls.append(source_url)
            if len(candidate_urls) >= max(1, self.config.research_page_limit):
                break

        pages: list[dict[str, Any]] = []
        for source_url in candidate_urls:
            try:
                extracted = self.extract(url=source_url)
            except Exception as exc:
                pages.append({"url": source_url, "error": str(exc)})
                continue
            excerpt = (extracted.get("content") or "").strip()
            excerpt_limit = max(200, self.config.research_excerpt_chars)
            if len(excerpt) > excerpt_limit:
                excerpt = excerpt[: excerpt_limit - 3] + "..."
            page_payload = {
                "url": source_url,
                "provider": extracted.get("provider", "groksearch-mcp"),
                "excerpt": excerpt,
            }
            if extracted.get("warning"):
                page_payload["warning"] = extracted["warning"]
            pages.append(page_payload)

        evidence = {
            "source_count": len(source_items),
            "page_count": len([page for page in pages if not page.get("error")]),
            "provider": "groksearch-mcp",
            "verification": "single-provider",
        }

        payload: dict[str, Any] = {
            "provider": "groksearch-mcp",
            "query": query.strip(),
            "platform": platform.strip(),
            "search": search_payload,
            "pages": pages,
            "evidence": evidence,
        }

        if map_url:
            payload["map"] = self.map(
                url=map_url,
                instructions=map_instructions,
                max_depth=max_depth,
                max_breadth=max_breadth,
                limit=limit,
                timeout=timeout,
            )
        return payload

    def health_sync(self) -> dict[str, Any]:
        return self.health()

    def get_config_info_sync(self) -> dict[str, Any]:
        return self.get_config_info()

    def search_sync(
        self,
        *,
        query: str,
        platform: str = "",
        model: str = "",
        extra_sources: int = 0,
    ) -> dict[str, Any]:
        return self.search(
            query=query,
            platform=platform,
            model=model,
            extra_sources=extra_sources,
        )

    def extract_sync(self, *, url: str) -> dict[str, Any]:
        return self.extract(url=url)

    def map_sync(
        self,
        *,
        url: str,
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        timeout: int = 150,
    ) -> dict[str, Any]:
        return self.map(
            url=url,
            instructions=instructions,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            timeout=timeout,
        )

    def research_sync(
        self,
        *,
        query: str,
        platform: str = "",
        extra_sources: int = 3,
        map_url: str = "",
        map_instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        timeout: int = 150,
    ) -> dict[str, Any]:
        return self.research(
            query=query,
            platform=platform,
            extra_sources=extra_sources,
            map_url=map_url,
            map_instructions=map_instructions,
            max_depth=max_depth,
            max_breadth=max_breadth,
            limit=limit,
            timeout=timeout,
        )
