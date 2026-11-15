# 用 DSH 下棋

让 [DSH(DeepSeek Harness)](https://github.com/deepseek-ai/deepseek-harness) 通过 MCP 连上货币战争 agent,由 DSH 里的模型来做决策。设计见 `docs/TECH_DESIGN.md` 11.9。

## 准备

1. 装游戏的电脑上运行 `cwagent serve`(或便携包的 `start.bat`)。DSH 在另一台电脑上时,`config.yaml` 里 `server.host` 要设成 `0.0.0.0`,并放行 8770 端口。
2. 把 `cwagent.cordis.yml` 接进 DSH,二选一:
   - 每次启动时带上:`dsh web --patch /path/to/cwagent.cordis.yml`
   - 一直生效:把文件里 `insert` 下的那一项合并进 `$DSH_HOME/profiles/<profile>/cordis.patch.yml`(DSH_HOME 默认 `~/.dsh`)。不要整个覆盖,里面可能有你自己的设置。
3. DSH 不在装游戏的电脑上时,把文件里的 `127.0.0.1` 换成那台电脑的局域网 IP。

启动后,DSH 的工具列表里会出现 `mcp__cwagent__rules`、`mcp__cwagent__start_run` 等工具。

## 开始

在 DSH 里新建会话,发:

> 用 cwagent 的工具开始一局货币战争,一直下到对局结束。

也可以先在网页对局页选「外部 agent(MCP)」点开始,再让 DSH 调用 `wait` 接手。

一局有几十个决策点,模型中途停下来时,用 `/goal` 设一个目标(例如「下完这一局货币战争」),DSH 会自动接着下。

## 说明

- 地址里的 `wait=540` 是没有需要判断的局面时(战斗、动画)一次工具调用最多等几秒,要小于 `toolCallTimeoutMs`(毫秒)。等待期间模型不会被调用。
- 网页对局页照常显示每一步的截图、交给模型的局面和模型的分析与操作;模型的思考过程在 DSH 自己的界面里看。
- 局面交出去后 15 分钟没有任何动静,对局会暂停。
