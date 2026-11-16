# 用 Claude Code 下棋

Claude Code 插件:连上货币战争 agent 的 MCP 服务,由 Claude Code 里的模型(用你自己的订阅或 API 额度)来做决策。设计见 `docs/TECH_DESIGN.md` 11.9。

## 准备

1. 装游戏的电脑上运行 `cwagent serve`(或便携包的 `start.bat`)。Claude Code 在另一台电脑上时,`config.yaml` 里 `server.host` 要设成 `0.0.0.0`,并放行 8770 端口。
2. 在 Claude Code 里装插件:

   ```
   /plugin install cwagent --marketplace 64673a/hsr-currency-wars-agent
   ```

   装的时候会问「cwagent 地址」。Claude Code 和游戏在同一台电脑上就用默认的 `http://127.0.0.1:8770`,否则填装游戏那台电脑的地址,如 `http://192.168.0.200:8770`(末尾不带 `/`)。以后可以在 `/config` 里改。

   用仓库里的源码试用:`claude --plugin-dir integrations/claude-code`。

## 开始

```
/cwagent:play A8-1
```

难度可以不写,不写就用设置页里的。也可以先在网页对局页选「外部 agent(MCP)」点开始,再运行 `/cwagent:play` 接手。

- 对局没结束时 Claude 想停下来,插件会让它接着下;对局结束或暂停后才会停。中途想停,直接告诉它停下(它会调用 `stop_run`),或在网页上点停止。
- `/cwagent:play` 只在它运行的那一轮免去工具确认。中途插话以后再弹确认时选「不再询问」,或在 `/permissions` 里允许 `mcp__plugin_cwagent_cwagent`。
- 网页对局页照常显示每一步的截图、交给模型的局面和模型的分析与操作;模型的思考过程在 Claude Code 里看。
- 新回合只发送新增事实和经历。上下文压缩后调用 `rules`、`snapshot` 恢复完整档案与最近截图。
- 局面交出去后 15 分钟没有任何动静,对局会暂停。
