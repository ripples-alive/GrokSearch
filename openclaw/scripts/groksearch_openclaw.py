#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


BASE_DIR = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_mapping_env(raw_env: dict[str, object]) -> None:
    for key, value in raw_env.items():
        if isinstance(value, str) and value.strip():
            os.environ.setdefault(key, value.strip())


def _load_openclaw_skill_env(base_dir: Path) -> None:
    candidates = []
    explicit = os.getenv("OPENCLAW_CONFIG_PATH", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(base_dir.parents[1] / "openclaw.json")

    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        env = (((config.get("skills") or {}).get("entries") or {}).get("grok-search") or {}).get("env") or {}
        if isinstance(env, dict):
            _load_mapping_env(env)
            return


def _bootstrap_env() -> None:
    _load_openclaw_skill_env(BASE_DIR)
    _load_env_file(BASE_DIR / ".env")


def _mcp_url() -> str:
    explicit = os.getenv("GROKSEARCH_MCP_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = os.getenv("GROKSEARCH_MCP_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL is required")
    return f"{base}/mcp"


def _health_url() -> str:
    explicit = os.getenv("GROKSEARCH_MCP_URL", "").strip()
    if explicit:
        root = explicit.rsplit("/", 1)[0] if "/" in explicit.rstrip("/") else explicit.rstrip("/")
        return f"{root}/health"
    base = os.getenv("GROKSEARCH_MCP_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL is required")
    return f"{base}/health"


def _headers(include_sse: bool = False) -> dict[str, str]:
    headers = {
        "User-Agent": os.getenv("GROKSEARCH_HTTP_USER_AGENT", "GrokSearch-OpenClaw/0.1").strip() or "GrokSearch-OpenClaw/0.1"
    }
    token = os.getenv("GROKSEARCH_MCP_BEARER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if include_sse:
        headers["Accept"] = "text/event-stream"
    return headers


def _waf_note(status: int, body: str) -> str | None:
    body_lower = body.lower()
    if status == 403 and "cloudflare" in body_lower and "1010" in body_lower:
        return (
            "remote endpoint is reachable but blocked by Cloudflare/WAF "
            "(error 1010). This is not a local OpenClaw install failure."
        )
    if status == 403 and "cloudflare" in body_lower:
        return "remote endpoint is reachable but blocked by Cloudflare/WAF."
    return None


def _request(url: str, headers: dict[str, str]) -> tuple[int, str]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc


def cmd_health() -> int:
    url = _health_url()
    status, body = _request(url, _headers())
    payload = {"url": url, "status_code": status, "body": body}
    note = _waf_note(status, body)
    if note:
        payload["note"] = note
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == 200 else 1


def cmd_probe() -> int:
    url = _mcp_url()
    status, body = _request(url, _headers(include_sse=True))
    payload = {
        "url": url,
        "status_code": status,
        "body": body,
        "user_agent": _headers(include_sse=True).get("User-Agent", ""),
    }
    note = _waf_note(status, body)
    if note:
        payload["note"] = note
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status in {200, 400, 401, 406} else 1


def main() -> int:
    _bootstrap_env()

    parser = argparse.ArgumentParser(description="OpenClaw helper for remote GrokSearch MCP")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("probe")
    args = parser.parse_args()

    if args.command == "health":
        return cmd_health()
    if args.command == "probe":
        return cmd_probe()
    return 2


if __name__ == "__main__":
    sys.exit(main())
