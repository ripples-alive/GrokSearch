---
name: grok-search
description: >-
  Default search skill for OpenClaw. Uses a bundled runtime to call a shared
  remote GrokSearch MCP endpoint for current web search, source retrieval, page
  extraction, site mapping, and lightweight research. Prefer this over legacy
  Tavily-only search layers or raw web_search when GrokSearch is healthy.
author: Codex
license: MIT
repository: https://github.com/GuDaStudio/GrokSearch
homepage: https://github.com/GuDaStudio/GrokSearch/tree/main/openclaw
security_disclosure: |
  This skill sends queries and the configured bearer token to the remote
  GrokSearch MCP endpoint. The bundled installer only copies local runtime files
  from this bundle and does not download remote code or modify other installed
  skills. Prefer injecting env vars through OpenClaw skill config instead of
  copying a .env file into the installed skill folder. Only point
  GROKSEARCH_MCP_BASE_URL or GROKSEARCH_MCP_URL at a host you trust.
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
      - docs
      - mcp
      - grok
      - tavily
---

# GrokSearch For OpenClaw

GrokSearch 是给 OpenClaw 用的默认搜索 skill。

如果用户只给了仓库地址或 `openclaw/` 目录：

- 先打开 `openclaw/README.md`
- 按 `README` 完成安装与验收
- 再回到这个 `SKILL.md` 执行搜索规则和使用策略

这个 bundle 现在自带 runtime，不只是远端 MCP 配置包装层。

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

1. 先跑：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py health
```

2. 再跑：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py search \
  --query "OpenAI latest announcements"
```

3. 如果公网只开放了 `/mcp`，仍可额外跑 MCP endpoint 探测：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py probe
```

4. 如果你要验证正文抓取：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py extract \
  --url "https://platform.openai.com/docs/overview"
```

5. 如果你要验证站点结构：

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py map \
  --url "https://platform.openai.com/docs" \
  --instructions "only documentation pages"
```

6. 如果 `probe` 返回 `400` / `401` / `406`，说明远程 MCP 入口基本可达。
7. 如果返回 Cloudflare 1010 / WAF 拦截，说明远端把当前出口 IP / UA 挡掉了，不是本地安装失败。

## GrokSearch-First 规则

只要远程 GrokSearch endpoint 健康：

- 外部搜索优先走 GrokSearch
- 官方文档、网页正文、站点结构优先继续用 GrokSearch，不要先切回别的搜索栈
- 不要优先走旧的 Tavily-only skill
- 只有远程 endpoint 不可达、认证失败或用户明确要求时，才回退到别的搜索方式

## 当前能力

这个 bundle 当前已经具备：

- 本地 runtime
- 远端 MCP 调用能力
- `search / extract / map / research / health / probe` 命令入口

## 常用命令

### 健康检查

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py health
```

### 普通网页搜索

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py search \
  --query "best MCP search server"
```

### 网页正文抓取

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py extract \
  --url "https://platform.openai.com/docs/overview"
```

### 站点结构映射

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py map \
  --url "https://platform.openai.com/docs" \
  --instructions "only documentation pages"
```

### 轻量 research

```bash
python3 {baseDir}/scripts/groksearch_openclaw.py research \
  --query "OpenAI Responses API latest changes" \
  --extra-sources 3
```
