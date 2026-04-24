# GrokSearch OpenClaw Plugin

[Back to repo](../README.md)

`openclaw/` 现在是一个可直接安装的 `OpenClaw` 原生插件目录，而不只是一个单独的 skill 包。

这一层包含三部分：

- 原生 plugin 入口
  - `openclaw.plugin.json`
  - `package.json`
  - `index.js`
  - `plugin-runtime.js`
- Agent 使用策略
  - `skills/grok-search/SKILL.md`
- 可选运维脚本
  - `scripts/groksearch_openclaw.py`
  - `runtime/groksearch/*`

核心设计是：

- 远端只暴露统一的 GrokSearch MCP 服务
- OpenClaw 本地安装这个 plugin
- plugin 把远端 MCP 能力注册成 OpenClaw 原生 `tool`
- 同时把 GrokSearch 接进 OpenClaw 的 `web_search` / `web_fetch` provider 位

## 当前能力

这个 plugin 现在会注册：

- `groksearch_search`
- `groksearch_sources`
- `groksearch_extract`
- `groksearch_map`
- `groksearch_research`

同时注册：

- `web_search` provider: `groksearch`
- `web_fetch` provider: `groksearch`

以及显式工具：

- `groksearch_search`
- `groksearch_extract`
- `groksearch_map`
- `groksearch_research`
- `groksearch_sources`

## 推荐最小配置

推荐最小配置只有两项：

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

默认 MCP URL 推导规则：

```text
{baseUrl}/mcp
```

如果你的远端路径不是 `/mcp`，改为：

```json
{
  "plugins": {
    "entries": {
      "grok-search": {
        "config": {
          "mcp": {
            "url": "https://search.example.com/custom-path",
            "bearerToken": "your-token"
          }
        }
      }
    }
  }
}
```

如果你没有公开 `/health`，可以不配 `healthUrl`。只有在健康检查路径和 `/mcp` 不同且需要显式覆盖时才补这一项。

## 安装方式

安装本地 plugin：

```bash
openclaw plugins install /path/to/GrokSearch/openclaw
```

OpenClaw 会读取：

- `package.json`
- `openclaw.plugin.json`
- `skills/`

并按 plugin 方式加载，而不是只把它当成普通 skill 文本。

## 验收

先测最小连通性：

```bash
node openclaw/scripts/test_plugin_tool.mjs \
  probe \
  '{}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
```

再测一轮真实搜索：

```bash
node openclaw/scripts/test_plugin_tool.mjs search \
  '{"query":"OpenAI latest announcements"}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
```

如果你要测正文抓取：

```bash
node openclaw/scripts/test_plugin_tool.mjs extract \
  '{"url":"https://platform.openai.com/docs/overview"}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
```

如果你要测站点结构：

```bash
node openclaw/scripts/test_plugin_tool.mjs map \
  '{"url":"https://platform.openai.com/docs","instructions":"only documentation pages"}' \
  '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'
```

如果你还想用附带的 Python 脚本单独验证远端 MCP，也可以：

```bash
python3 openclaw/scripts/groksearch_openclaw.py health
```

这些 `probe` / `health` / `config` 能力保留在本地运维脚本层，用于安装验证和排障，不会注册成公开的 Agent tool。

## 关于 `probe`

- `probe` 只检查 `/mcp` 连通性
- 它不是主搜索入口
- 对 MCP 来说，`400` / `401` / `406` 也可能表示“入口可达，但当前请求不是完整 MCP 会话”
- 如果返回 Cloudflare 1010 / WAF 拦截，这表示远端在，但当前出口 IP / UA 被挡了，不是本地 plugin 安装失败

## 兼容性说明

这个 plugin 现在优先读取：

- `plugins.entries.grok-search.config`

同时，附带的 Python 诊断脚本仍兼容读取旧的：

- `skills.entries.grok-search.env`

所以已有老配置不一定需要立刻迁移，但新部署建议统一走 plugin config。

## 插件暴露的能力

安装后，插件会暴露以下显式工具：

- `groksearch_search`
- `groksearch_extract`
- `groksearch_map`
- `groksearch_research`
- `groksearch_sources`

如果宿主启用了对应的 provider 路由能力，也可以把它接到宿主的 `web_search` / `web_fetch` 入口；这部分是否启用、如何路由，属于宿主侧配置，不在本插件文档中做强约束。

如果是安装排障或公网 MCP 可达性验证，交给运维脚本：

- `node openclaw/scripts/test_plugin_tool.mjs probe '{}' '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'`
- `node openclaw/scripts/test_plugin_tool.mjs health '{}' '{"mcp":{"baseUrl":"https://search.example.com","bearerToken":"your-token"}}'`
- `python3 openclaw/scripts/groksearch_openclaw.py config`
