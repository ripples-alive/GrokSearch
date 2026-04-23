---
name: grok-search
description: >-
  Default remote search skill for OpenClaw. Use when OpenClaw needs a shared
  GrokSearch MCP endpoint for current web search, page extraction, site
  mapping, or GrokSearch installation and verification. Prefer this over raw
  web search when the shared GrokSearch endpoint is configured and healthy.
author: Codex
license: MIT
repository: https://github.com/GuDaStudio/GrokSearch
homepage: https://github.com/GuDaStudio/GrokSearch/tree/main/openclaw
security_disclosure: |
  This skill sends queries and the configured bearer token to the remote
  GrokSearch MCP endpoint. Prefer injecting env vars through OpenClaw skill
  config instead of copying a .env file into the installed skill folder. Only
  point GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL at a host you trust.
metadata:
  openclaw:
    emoji: "🔎"
    requires:
      bins:
        - bash
        - python3
      env:
        - GROKSEARCH_MCP_BASE_URL
        - GROKSEARCH_MCP_BEARER_TOKEN
    primaryEnv: GROKSEARCH_MCP_BEARER_TOKEN
    tags:
      - search
      - web
      - mcp
      - grok
      - tavily
---

# GrokSearch For OpenClaw

GrokSearch 是给 OpenClaw 用的远程搜索 skill。

如果用户只给了仓库地址或 `openclaw/` 目录：

- 先打开 `openclaw/README.md`
- 按 `README` 完成安装与验收
- 再回到这个 `SKILL.md` 执行搜索规则和使用策略

## 最小配置

推荐最小配置只有两项：

- `GROKSEARCH_MCP_BASE_URL`
- `GROKSEARCH_MCP_BEARER_TOKEN`

如果路径不是默认的 `/mcp`，再补：

- `GROKSEARCH_MCP_URL`

## OpenClaw 配置建议

优先把 env 注入到 OpenClaw skill 配置，而不是复制 `.env` 到 skill 目录。

推荐：

```json
{
  "skills": {
    "entries": {
      "grok-search": {
        "enabled": true,
        "env": {
          "GROKSEARCH_MCP_BASE_URL": "https://search.example.com",
          "GROKSEARCH_MCP_BEARER_TOKEN": "your-token"
        }
      }
    }
  }
}
```

## 安装

本地 bundle 安装：

```bash
bash {baseDir}/scripts/install_openclaw_skill.sh --install-to ~/.openclaw/skills/grok-search
```

## 验收顺序

1. 先跑健康检查：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py health
```

2. 再跑 MCP endpoint 探测：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py probe
```

3. 如果远程服务健康，再让 OpenClaw 通过配置好的 MCP 调实际搜索。

## GrokSearch-First 规则

只要远程 GrokSearch endpoint 健康：

- 外部搜索优先走 GrokSearch
- 官方文档、网页正文、站点结构优先继续用 GrokSearch，不要先切回别的搜索栈
- 只有远程 endpoint 不可达、认证失败或用户明确要求时，才回退到别的搜索方式

