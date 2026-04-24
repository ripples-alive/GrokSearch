# GrokSearch Skill

`skill/` 这一层的目标很简单：

- 让 `Codex` 安装并理解怎么使用 `GrokSearch`
- 让 `Claude Code` 在拿到仓库链接或远程 MCP URL 时，顺着文档把接入配好
- 优先支持“团队共享远程 HTTP MCP + Bearer Token”的接入方式

这里不是单独的 MCP 实现目录。真正的服务代码在仓库根目录和 `src/`。

## 适用场景

- 用户贴了这个仓库或 `skill/` 路径，希望 AI 自动安装和接入
- 团队已经部署了远程 GrokSearch MCP，希望成员统一接入
- 需要 Bearer Token 保护的公网 MCP

## 1. 安装 Codex Skill

```bash
bash skill/scripts/install_codex_skill.sh
```

如果要覆盖已有目录：

```bash
bash skill/scripts/install_codex_skill.sh --force
```

安装后重启 `Codex`。

## 2. 接入远程 HTTP MCP

这是团队共享部署的推荐方式。

### Codex

无认证：

```bash
codex mcp add grok-search --url https://search.example.com/mcp
```

带 Bearer Token：

```bash
export GROK_SEARCH_MCP_BEARER_TOKEN=your-token
codex mcp add grok-search \
  --url https://search.example.com/mcp \
  --bearer-token-env-var GROK_SEARCH_MCP_BEARER_TOKEN
```

### Claude Code

无认证：

```bash
claude mcp add \
  --transport http \
  grok-search \
  https://search.example.com/mcp
```

带 Bearer Token：

```bash
claude mcp add \
  --transport http \
  --header "Authorization: Bearer YOUR_TOKEN" \
  grok-search \
  https://search.example.com/mcp
```

## 3. 本地 stdio MCP

如果没有远程地址，仍然可以本地运行当前仓库：

```bash
claude mcp add-json grok-search --scope user '{
  "type": "stdio",
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/ripples-alive/GrokSearch",
    "grok-search"
  ],
  "env": {
    "GROK_API_URL": "https://your-api-endpoint.com/v1",
    "GROK_API_KEY": "your-grok-api-key",
    "TAVILY_API_KEY": "tvly-your-tavily-key"
  }
}'
```

## 4. 验收

先看配置是否注册成功：

```bash
codex mcp list
```

或者：

```bash
claude mcp list
```

然后让模型调用：

- `get_config_info`
- `web_search(query="OpenAI latest announcements")`

如果启用了 Tavily，再验证：

- `web_fetch(url="https://platform.openai.com/docs/overview")`

