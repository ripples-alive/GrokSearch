# GrokSearch OpenClaw Skill

[Back to repo](../README.md)

`openclaw/` 是给 `OpenClaw` 准备的独立 skill bundle。

这一层和 `skill/` 的区别很明确：

- `skill/`
  - 主要面向 `Codex` / `Claude Code`
- `openclaw/`
  - 面向 `OpenClaw`
  - 负责提供真正可安装的 OpenClaw skill bundle

当前这套 bundle 的推荐架构是：

- 团队先部署好远程 `GrokSearch` MCP
- OpenClaw skill 通过统一的远程 MCP URL + Bearer Token 接入

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

优先先跑健康检查：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py health
```

如果要检查 MCP endpoint 探测：

```bash
python3 ~/.openclaw/skills/grok-search/scripts/groksearch_openclaw.py probe
```

说明：

- `health` 会请求 `GET /health`
- `probe` 会请求远程 MCP URL，并显示 HTTP 状态
- MCP 协议入口不是普通 REST API，所以 `/mcp` 返回 `400` 或 `406` 也可能是正常的，只要不是连接失败或 5xx

## 本地调试

只在本地调试这个 bundle 时，才建议：

```bash
cp openclaw/.env.example openclaw/.env
python3 openclaw/scripts/groksearch_openclaw.py health
```

