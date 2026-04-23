#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE_DIR / "runtime"

if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from groksearch import GrokSearchClient, GrokSearchError  # noqa: E402


def _snippet(item: dict[str, object]) -> str:
    for key in ("description", "content", "summary", "text", "excerpt"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _result_lines(items: list[dict[str, object]], heading: str) -> list[str]:
    lines = [heading, ""]
    if not items:
        lines.append("- 无结果")
        lines.append("")
        return lines

    for index, item in enumerate(items, start=1):
        title = str(item.get("title") or item.get("url") or f"结果 {index}").strip()
        url = str(item.get("url") or "").strip()
        snippet = _snippet(item)

        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   {url}")
        if snippet:
            compact = " ".join(snippet.split())
            if len(compact) > 400:
                compact = f"{compact[:397]}..."
            lines.append(f"   {compact}")
        lines.append("")
    return lines


def _render_health(payload: dict[str, object]) -> str:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    probe = payload.get("probe") if isinstance(payload.get("probe"), dict) else {}
    remote_config = payload.get("remote_config") if isinstance(payload.get("remote_config"), dict) else {}
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []

    lines = [
        "# GrokSearch Health",
        "",
        f"- mcp_url: `{runtime.get('mcp_url', '')}`",
        f"- health_url: `{runtime.get('health_url', '')}`",
        f"- probe_status: `{probe.get('status_code', '')}`",
        f"- bearer_configured: `{runtime.get('has_bearer_token', False)}`",
        f"- user_agent: `{runtime.get('http_user_agent', '')}`",
    ]
    if payload.get("tool_error"):
        lines.append(f"- tool_error: {payload['tool_error']}")
    lines.extend(["", "## Tools", ""])
    if tools:
        for name in tools:
            lines.append(f"- `{name}`")
    else:
        lines.append("- 无法列出远端工具")
    if remote_config:
        lines.extend(
            [
                "",
                "## Remote Config",
                "",
                f"- transport: `{remote_config.get('GROK_MCP_TRANSPORT', '')}`",
                f"- model: `{remote_config.get('GROK_MODEL', '')}`",
                f"- log_level: `{remote_config.get('GROK_LOG_LEVEL', '')}`",
                f"- config_status: {remote_config.get('config_status', '')}",
            ]
        )
    return "\n".join(lines).strip()


def _render_search(payload: dict[str, object]) -> str:
    lines = [
        "# GrokSearch Search",
        "",
        f"- session_id: `{payload.get('session_id', '')}`",
        f"- sources_count: `{payload.get('sources_count', 0)}`",
        "",
    ]

    content = str(payload.get("content") or "").strip()
    if content:
        lines.extend(["## Answer", "", content, ""])

    sources = payload.get("sources")
    if isinstance(sources, list):
        lines.extend(_result_lines([item for item in sources if isinstance(item, dict)], "## Sources"))

    if payload.get("sources_error"):
        lines.extend(["## Source Error", "", str(payload["sources_error"]), ""])
    return "\n".join(lines).strip()


def _render_extract(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "# GrokSearch Extract",
            "",
            f"- url: {payload.get('url', '')}",
            "",
            "## Content",
            "",
            str(payload.get("content") or "").strip() or "(empty)",
        ]
    ).strip()


def _render_map(payload: dict[str, object]) -> str:
    lines = [
        "# GrokSearch Map",
        "",
        f"- url: {payload.get('url', '') or payload.get('base_url', '')}",
        f"- results_count: `{len(payload.get('results', [])) if isinstance(payload.get('results'), list) else 0}`",
        "",
    ]
    results = payload.get("results")
    if isinstance(results, list):
        lines.extend(_result_lines([item for item in results if isinstance(item, dict)], "## Results"))
    elif payload.get("raw"):
        lines.extend(["## Raw", "", str(payload["raw"]), ""])
    return "\n".join(lines).strip()


def _render_research(payload: dict[str, object]) -> str:
    lines = [
        "# GrokSearch Research",
        "",
        f"- query: {payload.get('query', '')}",
    ]
    search = payload.get("search")
    if isinstance(search, dict):
        lines.extend(
            [
                f"- session_id: `{search.get('session_id', '')}`",
                f"- sources_count: `{search.get('sources_count', 0)}`",
                "",
            ]
        )
        content = str(search.get("content") or "").strip()
        if content:
            lines.extend(["## Answer", "", content, ""])
        sources = search.get("sources")
        if isinstance(sources, list):
            lines.extend(_result_lines([item for item in sources if isinstance(item, dict)], "## Sources"))

    pages = payload.get("pages")
    if isinstance(pages, list):
        lines.extend(["## Pages", ""])
        if not pages:
            lines.append("- 无抓取页面")
            lines.append("")
        else:
            for index, page in enumerate(pages, start=1):
                if not isinstance(page, dict):
                    continue
                lines.append(f"{index}. {page.get('url', '')}")
                if page.get("error"):
                    lines.append(f"   error={page['error']}")
                excerpt = str(page.get("excerpt") or "").strip()
                if excerpt:
                    lines.append(f"   {excerpt}")
                lines.append("")

    map_payload = payload.get("map")
    if isinstance(map_payload, dict):
        lines.extend(["## Map", "", _render_map(map_payload), ""])
    return "\n".join(lines).strip()


def _emit(payload: dict[str, object], *, fmt: str, renderer) -> None:
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(renderer(payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw wrapper for remote GrokSearch MCP")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe")
    sub.add_parser("health")

    search_parser = sub.add_parser("search", help="Run a GrokSearch web search")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--platform", default="")
    search_parser.add_argument("--model", default="")
    search_parser.add_argument("--extra-sources", type=int, default=0)

    extract_parser = sub.add_parser("extract", help="Extract a single URL")
    extract_parser.add_argument("--url", required=True)

    map_parser = sub.add_parser("map", help="Map a website")
    map_parser.add_argument("--url", required=True)
    map_parser.add_argument("--instructions", default="")
    map_parser.add_argument("--max-depth", type=int, default=1)
    map_parser.add_argument("--max-breadth", type=int, default=20)
    map_parser.add_argument("--limit", type=int, default=50)
    map_parser.add_argument("--timeout", type=int, default=150)

    research_parser = sub.add_parser("research", help="Run GrokSearch search plus follow-up fetches")
    research_parser.add_argument("--query", required=True)
    research_parser.add_argument("--platform", default="")
    research_parser.add_argument("--extra-sources", type=int, default=3)
    research_parser.add_argument("--map-url", default="")
    research_parser.add_argument("--map-instructions", default="")
    research_parser.add_argument("--max-depth", type=int, default=1)
    research_parser.add_argument("--max-breadth", type=int, default=20)
    research_parser.add_argument("--limit", type=int, default=50)
    research_parser.add_argument("--timeout", type=int, default=150)

    args = parser.parse_args()
    client = GrokSearchClient()

    try:
        if args.command == "probe":
            payload = client.probe()
            _emit(payload, fmt=args.format, renderer=lambda item: json.dumps(item, ensure_ascii=False, indent=2))
            return 0 if payload.get("ok") else 1
        if args.command == "health":
            payload = client.health_sync()
            _emit(payload, fmt=args.format, renderer=_render_health)
            return 0 if payload.get("probe", {}).get("ok") else 1
        if args.command == "search":
            payload = client.search_sync(
                query=args.query,
                platform=args.platform,
                model=args.model,
                extra_sources=args.extra_sources,
            )
            _emit(payload, fmt=args.format, renderer=_render_search)
            return 0
        if args.command == "extract":
            payload = client.extract_sync(url=args.url)
            _emit(payload, fmt=args.format, renderer=_render_extract)
            return 0
        if args.command == "map":
            payload = client.map_sync(
                url=args.url,
                instructions=args.instructions,
                max_depth=args.max_depth,
                max_breadth=args.max_breadth,
                limit=args.limit,
                timeout=args.timeout,
            )
            _emit(payload, fmt=args.format, renderer=_render_map)
            return 0
        if args.command == "research":
            payload = client.research_sync(
                query=args.query,
                platform=args.platform,
                extra_sources=args.extra_sources,
                map_url=args.map_url,
                map_instructions=args.map_instructions,
                max_depth=args.max_depth,
                max_breadth=args.max_breadth,
                limit=args.limit,
                timeout=args.timeout,
            )
            _emit(payload, fmt=args.format, renderer=_render_research)
            return 0
    except GrokSearchError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
