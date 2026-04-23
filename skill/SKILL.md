---
name: grok-search
description: >-
  Install, verify, debug, and use GrokSearch as a team-shared search MCP. Use
  when the user shares a GrokSearch repo or skill URL, wants GrokSearch
  installed or repaired, or wants current web search, page extraction, site
  mapping, or search planning through a shared remote MCP endpoint. Prefer this
  over generic web search when the GrokSearch MCP is configured and healthy.
allowed-tools: mcp__grok-search__web_search, mcp__grok-search__get_sources, mcp__grok-search__web_fetch, mcp__grok-search__web_map, mcp__grok-search__get_config_info, mcp__grok-search__switch_model, mcp__grok-search__toggle_builtin_tools, mcp__grok-search__plan_intent, mcp__grok-search__plan_complexity, mcp__grok-search__plan_sub_query, mcp__grok-search__plan_search_term, mcp__grok-search__plan_tool_mapping, mcp__grok-search__plan_execution
---

# GrokSearch

GrokSearch 既可以本地 `stdio` 运行，也可以作为团队共享的远程 HTTP MCP 使用。

如果用户只给了这个仓库、`skill/` 目录，或者一个已经部署好的 GrokSearch HTTP 地址：

- 先打开 `skill/README.md`
- 按 `README` 完成安装与验收
- 再按这个 `SKILL.md` 使用工具

## GrokSearch-First 规则

只要 `grok-search` MCP 已注册且可调用：

- 外部搜索任务优先走 `GrokSearch`
- 官方文档、公告、网页正文优先走 `web_search` + `web_fetch`
- 站点结构或目录页优先走 `web_map`
- 复杂搜索任务优先先走 `plan_intent`
- 不要先混用通用网页搜索或别的 search MCP

只有在下面情况，才回退到通用网页搜索：

- `grok-search` MCP 未注册
- 远程 GrokSearch endpoint 不可达
- 认证失败且当前无法补齐 token
- `web_fetch` / `web_map` 因缺少 Tavily 配置而不可用
- 用户明确要求再用别的搜索工具复核

## 用户给的是 Skill 地址时怎么处理

如果用户给的是：

- 仓库根目录
- `skill/` 目录链接
- 本地 `skill/` 路径

默认按下面顺序处理：

1. 先安装 skill 到 `~/.codex/skills/grok-search`
2. 再确认 `grok-search` MCP 是否已经注册
3. 如果用户给的是远程 HTTP MCP URL，就按远程 MCP 处理
4. 如果用户没有远程 URL，再按本地 `stdio` 安装
5. 安装完成后提醒用户重启 `Codex`

## 用户给的是远程 GrokSearch URL 时怎么处理

如果用户给的是已经部署好的远程地址，例如：

- `https://search.example.com/mcp`
- `http://127.0.0.1:8000/mcp`

默认按下面顺序处理：

1. 把它当成远程 MCP，不要再让用户本地执行 Docker 或 `./install.sh`
2. 如果当前环境是 `Codex`，优先执行：

```bash
codex mcp add grok-search --url https://search.example.com/mcp
```

3. 如果需要 Bearer Token，优先使用：

```bash
export GROK_SEARCH_MCP_BEARER_TOKEN=your-token
codex mcp add grok-search \
  --url https://search.example.com/mcp \
  --bearer-token-env-var GROK_SEARCH_MCP_BEARER_TOKEN
```

4. 如果当前环境是 `Claude Code`，使用：

```bash
claude mcp add \
  --transport http \
  grok-search \
  https://search.example.com/mcp
```

5. 如果 `Claude Code` 需要 Bearer Token，使用：

```bash
claude mcp add \
  --transport http \
  --header "Authorization: Bearer YOUR_TOKEN" \
  grok-search \
  https://search.example.com/mcp
```

## 安装后验收

优先按下面顺序验收：

1. `codex mcp list` 或 `claude mcp list`
2. `codex mcp get grok-search` 或检查 Claude 配置
3. 让模型调用 `get_config_info`
4. 再做最小烟测：
   - `web_search(query="OpenAI latest announcements")`
   - 如果 Tavily 已配置，再测 `web_fetch(url="https://platform.openai.com/docs/overview")`

## 参数规则

- 复杂搜索先 `plan_intent`
- `web_search` 的 `extra_sources` 默认保持小值，不要无意义拉高
- 读取正文时优先直接 `web_fetch(url=...)`
- 站点级发现优先 `web_map`
- 需要引用来源时用 `get_sources(session_id=...)`

## 用户可见输出规则

- 不要向用户逐条播报“我先打开了 skill / 调用了哪个 MCP 工具”
- 只汇报真正有意义的动作，例如：
  - “我先查官方来源，再抓正文核对。”
  - “我先确认远程 GrokSearch 可用，再做一轮搜索。”

