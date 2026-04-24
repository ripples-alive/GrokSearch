---
name: grok-search-plugin-bundle
description: OpenClaw GrokSearch plugin bundle. Install this plugin from the openclaw directory, configure the remote MCP endpoint, and let OpenClaw use the bundled native tools and provider-backed skills.
metadata:
  { "openclaw": { "emoji": "🔎" } }
---

# GrokSearch OpenClaw Bundle

This directory is an OpenClaw plugin bundle, not only a plain skill bundle.

## What Lives Here

- `openclaw.plugin.json`
  - OpenClaw plugin manifest
- `package.json`
  - Plugin package metadata and runtime dependencies
- `index.js`
  - Native plugin entry that registers tools and web providers
- `plugin-runtime.js`
  - Pure-JS MCP runtime used by the plugin itself
- `skills/grok-search/SKILL.md`
  - Agent-facing usage policy for the registered tools/providers
- `scripts/test_plugin_tool.mjs`
  - Local JS helper for manual MCP probe/search validation

## What This Bundle Enables

After installation, OpenClaw can use GrokSearch in two ways:

1. As native OpenClaw tools:
   - `groksearch_search`
   - `groksearch_sources`
   - `groksearch_extract`
   - `groksearch_map`
   - `groksearch_research`
2. As generic OpenClaw providers:
   - `web_search` provider id: `groksearch`
   - `web_fetch` provider id: `groksearch`

## Installation

Install the plugin directory into OpenClaw:

```bash
openclaw plugins install {baseDir}
```

## Configuration

Prefer plugin config under `plugins.entries.grok-search.config`.

Minimum config:

```json
{
  "plugins": {
    "entries": {
      "grok-search": {
        "enabled": true,
        "config": {
          "mcp": {
            "baseUrl": "https://search.example.com",
            "bearerToken": "your-token"
          }
        }
      }
    }
  }
}
```

If the MCP endpoint is not served at `/mcp`, set:

```json
{
  "plugins": {
    "entries": {
      "grok-search": {
        "config": {
          "mcp": {
            "url": "https://search.example.com/custom-mcp-path"
          }
        }
      }
    }
  }
}
```

Legacy env compatibility has been removed from this bundle. Plugin config is the only supported path.

## Validation

Recommended smoke tests after install:

```bash
node {baseDir}/scripts/test_plugin_tool.mjs \
  probe \
  '{}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
node {baseDir}/scripts/test_plugin_tool.mjs \
  search \
  '{"query":"OpenAI latest announcements"}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
```

These diagnostics stay at the local JS helper layer and are intentionally not registered as public agent tools.

## Runtime Policy

After install, agent usage policy comes from:

```text
{baseDir}/skills/grok-search/SKILL.md
```
