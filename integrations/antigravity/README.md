# 用 Antigravity 下棋

Antigravity 插件:通过 Streamable HTTP MCP 连上装游戏那台电脑上的 `cwagent serve`,由 Antigravity(App / IDE / CLI)里的模型做决策。设计见 `docs/TECH_DESIGN.md` 11.9。

插件打包了三部分:
- `mcp_config.json`:接入 `cwagent` MCP 服务;
- `skills/cwagent-play/SKILL.md`:下棋与接手流程说明;
- `hooks.json` + `scripts/hook.py`:对局未结束时自动让当前会话继续下(`PostToolUse` 认领会话、`Stop` 返回 `{"decision": "continue", "reason": ...}`)。

## 安装

1. 装游戏的电脑上运行 `cwagent serve`(或便携包的 `start.bat`)。Antigravity 在另一台电脑上时,`config.yaml` 里 `server.host` 要设成 `0.0.0.0`,并放行 8770 端口(或通过 SSH 端口转发到本机)。
2. 在运行 Antigravity 的电脑上安装全局插件:

   ```bash
   python3 integrations/antigravity/install.py --url http://127.0.0.1:8770
   ```

   跨电脑时把地址换成装游戏那台电脑的局域网 IP,例如 `http://192.168.0.200:8770`(也可以直接写带 `/mcp` 的完整地址)。Windows 上没有 `python3` 时用 `uv run python integrations/antigravity/install.py --url ...`。

   脚本会把插件装到 `~/.gemini/config/plugins/cwagent/`,并把当前 Python 路径写入 `hooks.json`(不依赖 `cwagent` 虚拟环境,只用标准库)。不想安装自动续行 hook 时加 `--no-hooks`。
3. **重启 Antigravity** 使新插件目录被加载。之后可以在 Customizations / Plugins 页面随时开关整个插件(不用下棋时关闭即可,连 MCP 和 hook 一起停用)。

如果只想手动配置而不装插件,可以把 `mcp_config.json` 里的 `cwagent` 一项合并进 `~/.gemini/config/mcp_config.json`,并把 `skills/cwagent-play/SKILL.md` 复制到 `~/.gemini/config/skills/cwagent-play/SKILL.md`。

## 开始

重启后,可以先让 Antigravity 调用 `cwagent` 的 `status` 和 `observe` 验证连通性和读图(它们不会开始对局、不发键鼠)。

确认正常后直接发:

> 用 cwagent-play 打一局标准博弈 A8-1

也可以先在网页对局页把模型选成「外部 agent(MCP)」点开始,再让 Antigravity 用 `cwagent-play` 接手。

## 说明

- **自动续行**:只有调用过 `cwagent` 对局工具的会话才会写本机标记;其他普通会话的 `Stop` hook 直接放行、不会连 `cwagent serve`。用户手动取消(`USER_CANCELED`)、出错、后台任务未完成、对局已结束、或换了别的会话接手时都会放行。如果安装时加了 `--no-hooks` 或手动配置了 MCP,模型中途停下来时可以用 Antigravity 的 `/goal` 命令接着下。
- **长等待(`wait`)**:Antigravity 的 `mcp_config.json` 文档未提供自定义工具超时字段,默认不带 `?wait=1800`,沿用服务端默认的 50 秒等待(低于常见客户端 60 秒工具超时)。如果实测所用版本允许更长的 MCP 工具调用,安装时可用 `--wait <秒>` 调大(上限 3600,建议不超过 1800),或在 `mcp_config.json` 中修改 URL 查询参数。
- **网页与压缩恢复**:网页对局页照常显示每一步截图、局面和模型的分析与操作。上下文压缩或换会话接手后调用 `rules` 和 `snapshot` 恢复完整档案与最近截图。
- **空闲超时**:局面交出去后 15 分钟没有任何动静,对局会自动暂停。

## 验证状态

- 非 Windows 平台单元测试已验证:`install.py` 安装与地址规范化、`scripts/hook.py` 对 Eager / Lazy MCP 调用的识别、与真实 `/mcp` 端点的 `hook` 通信、跨会话隔离、取消放行与连续续行上限。
- 待实测:Antigravity 客户端实际加载本插件后的 MCP 工具调用超时上限、`PostToolUse` 实际传入的 MCP 工具名格式、Windows 实机 `observe` 读图与完整对局。
