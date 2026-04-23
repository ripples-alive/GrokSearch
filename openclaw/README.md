# GrokSearch OpenClaw Skill

[Back to repo](../README.md)

`openclaw/` 是给 `OpenClaw` 准备的独立 skill bundle。

这一层和 `skill/` 的区别很明确：

- `skill/`
  - 主要面向 `Codex` / `Claude Code`
- `openclaw/`
  - 面向 `OpenClaw`
  - 负责提供真正可安装的 OpenClaw skill bundle

这个 bundle 现在自带本地 runtime，不再只是远端 MCP 连通性探测脚本。

当前推荐架构是：

- 团队先部署好远程 `GrokSearch` MCP
- OpenClaw skill 通过统一的远程 MCP URL + Bearer Token 接入
- 本地 wrapper 再把远端 MCP 包装成 OpenClaw 可直接执行的搜索命令

## 推荐最小配置

只配置下面两项即可：

```env
GROKSEARCH_MCP_BASE_URL=https://search.example.com
GROKSEARCH_MCP_BEARER_TOKEN=your-token
```

默认远程 MCP URL 会按下面规则推导：

```text
{GROKSEARCH_MCP_BASE_URL}/mcp
```

如果你的服务路径不是 `/mcp`，可以额外设置：

```env
GROKSEARCH_MCP_URL=https://search.example.com/custom-path
```

## 安装方式

复制本地 bundle：

```bash
bash openclaw/scripts/install_openclaw_skill.sh \
  --install-to ~/.openclaw/skills/grok-search
```

这个安装器会：

- 复制整个 `openclaw/` bundle
- 带上 bundled runtime
- 不下载远端代码
- 不修改其他 skill

安装后，优先通过 OpenClaw 的 skill env 注入配置，而不是把 secret 写进 skill 目录。

推荐的 OpenClaw skill env 示例：

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

## 验收

先测远端入口与 runtime：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py health
```

再跑一轮搜索 smoke test：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py search \
  --query "OpenAI latest announcements"
```

如果你要测正文抓取：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py extract \
  --url "https://platform.openai.com/docs/overview"
```

如果你要测站点结构：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py map \
  --url "https://platform.openai.com/docs" \
  --instructions "only documentation pages"
```

如果你公网只开放了 `/mcp`，不开放 `/health`，也仍然可以单独跑：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py --format json probe
```

说明：

- `probe` 会请求远程 MCP URL，并显示 HTTP 状态
- MCP 协议入口不是普通 REST API，所以 `/mcp` 返回 `400` 或 `406` 也可能是正常的，只要不是连接失败或 5xx
- 如果返回 Cloudflare 1010 / WAF 拦截，这说明远端可达，但当前出口 IP / UA 被挡了，不是本地 OpenClaw 安装失败
- `health` 会继续尝试列出远端 MCP 工具，并读取 `get_config_info`
- 这套 wrapper 现在提供 `health`、`search`、`extract`、`map`、`research`、`probe`

## 现在能做什么

这套 `openclaw/` 现在已经提供：

- `health`
- `search`
- `extract`
- `map`
- `research`
- `probe`

也就是说，OpenClaw 这边不再只有健康检查，而是已经有可实际执行的搜索、正文抓取、站点结构和轻量 research 入口。

## 本地调试

只在本地调试这个 bundle 时，才建议：

```bash
cp openclaw/.env.example openclaw/.env
python3 openclaw/scripts/groksearch_openclaw.py health
```
