# 用 Codex 连接 cwagent

Windows 上运行 `cwagent serve`,Codex 通过同一个 `/mcp` HTTP 端点接入。服务返回文字和原生 MCP 图片块,不需要另起一套服务或读取 Codex 登录凭据。

在运行 Codex 的电脑上安装:

```bash
uv run python integrations/codex/install.py --url 'http://127.0.0.1:8770/mcp?wait=45'
```

跨电脑时替换为 Windows 的 IP 地址,或使用 SSH 转发的本地地址。注册使用 Codex CLI 的 `codex mcp add`,保留其余服务器配置;skill 安装到 `$CODEX_HOME/skills/cwagent-play`。`config.example.toml` 给出可选的项目配置。

在重新加载 MCP 配置的 Codex 会话里,先调用 cwagent 的 `status`、`observe` 验证服务和图片。它们不会开始对局。要玩时明确说“用 $cwagent-play 打一局标准博弈 A8-1”。

- `act` 的 `turn` 对应返回的局面轮次,旧轮次不会重复执行。超时后用 `wait` 重取局面。
- 上下文压缩或换会话接手后调用 `rules` 和 `snapshot`。
- 返回暂停或错误时保留现场;返回对局结束后停止,不自动开下一局。
- `observe` 只截图、不发键鼠;必须在 Windows 用户桌面会话中的 serve 调用。

配置依据:OpenAI 官方 Codex MCP 文档 `https://developers.openai.com/codex/mcp`。
