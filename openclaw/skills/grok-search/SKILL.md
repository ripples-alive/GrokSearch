---
name: grok-search
description: OpenClaw-native GrokSearch plugin skill. Prefer generic web_search and web_fetch when GrokSearch is selected as the provider, and use explicit groksearch_* tools for source sessions, site mapping, health checks, and config inspection.
metadata:
  { "openclaw": { "emoji": "🔎", "requires": { "config": ["plugins.entries.grok-search.enabled"] } } }
---

# GrokSearch

Use GrokSearch as the default external search stack in OpenClaw when the `grok-search` plugin is installed and configured.

## What This Plugin Provides

- `web_search`
  - Generic OpenClaw search provider backed by GrokSearch
- `web_fetch`
  - Generic OpenClaw fetch provider backed by GrokSearch
- `groksearch_search`
  - GrokSearch-specific search with `platform`, `model`, and `extra_sources`
- `groksearch_sources`
  - Fetch the source list for a prior GrokSearch session
- `groksearch_extract`
  - Extract a single page through the remote MCP
- `groksearch_map`
  - Map a site or section through the remote MCP
- `groksearch_research`
  - Search plus follow-up extraction and optional mapping
- `groksearch_config`
  - Inspect remote config info
- `groksearch_health`
  - Run probe plus MCP tool/config checks
- `groksearch_probe`
  - Only test MCP reachability

## Preferred Routing

1. Use `web_search` for normal web lookup when GrokSearch is selected as the active search provider.
2. Use `web_fetch` for normal page extraction when GrokSearch is selected as the active fetch provider.
3. Use `groksearch_search` when you need GrokSearch-specific search knobs such as `platform`, `model`, or `extra_sources`.
4. Use `groksearch_sources` after a GrokSearch search when you need the full structured source list for a known `session_id`.
5. Use `groksearch_map` when the task is about site structure, navigation, or URL discovery rather than answer-first web search.

## When To Reach For Explicit Tools

- Need the exact GrokSearch `session_id` and source cache:
  - Use `groksearch_search`, then `groksearch_sources`
- Need site structure instead of page text:
  - Use `groksearch_map`
- Need to debug why GrokSearch is not being chosen:
  - Use `groksearch_health` first
  - Use `groksearch_probe` only for endpoint reachability checks
- Need to confirm remote MCP settings:
  - Use `groksearch_config`

## Usage Notes

- `web_search` and `web_fetch` are the right default choices once `tools.web.search.provider` or `tools.web.fetch.provider` is set to `groksearch`.
- `groksearch_probe` is not a search tool. It is only for diagnosing whether the remote `/mcp` endpoint is reachable.
- A `400`, `401`, or `406` from `/mcp` can still mean the MCP endpoint is reachable. Reachability and a valid MCP session are different checks.
- If the remote edge blocks the current IP or User-Agent, use `groksearch_probe` or `groksearch_health` to confirm that failure mode before falling back to another search provider.
