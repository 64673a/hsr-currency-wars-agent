# AGENTS.md

## 项目

- 让 LLM 自主游玩《崩坏:星穹铁道》货币战争 S2「入画奇航」的 agent。
- 权威设计文档:`docs/TECH_DESIGN.md`。开始任何工作前先读它;第 1.6 节是已确定的设计决策,不要擅自改。
- 现状:正在做第一个里程碑(M0)。已完成:配置管理(厂商 + 模型档案两层)、LLM 接入层(两种格式、模型列表、连通性测试)、知识同步(`cwagent knowledge sync`)、截图模块(`capture/`)、截图采集(`cwagent collect`,已在 Windows 实测)、区域校对(`cwagent regions check`)、记忆系统的存储与检索(`cwagent memory`)、后端接口(`cwagent serve`)、前端(`web/`,设置页和知识库页)。M0 完成,下一步 M1 感知。
- 流程不能写死:投资环境、投资策略、奇遇伙伴都会改变节点结构和出现的界面(设计文档 2.3)。奇遇伙伴数据官方接口没有,也无法事先采集全:对局中遇到一个记一个(本机 `data/companions.local.yaml`),核对后导出到 `knowledge/companions.yaml`(`cwagent companions`)。
- 记忆系统(设计文档 5.4):公共记忆在 `knowledge/memories.yaml`(随仓库发布),本机学到的在 `data/memory.sqlite`。维护者口头告诉你的游戏经验,整理成记忆写进 `knowledge/memories.yaml`,并注明来源和是否已核实。
- 运行形态:agent 作为一个服务运行在装有游戏的 Windows 上,像 March7thAssistant 一样直接定位游戏窗口截图、发送键鼠。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 设计文档 |
| `src/cwagent/` | agent 本体(按设计文档第 15 节的结构逐步实现) |
| `pyproject.toml`、`uv.lock`、`.python-version` | uv 项目配置 |
| `config.example.yaml` | 配置示例,复制为 `config.yaml`(不入库)后使用 |
| `web/` | 前端:React 19 + Vite + TypeScript + Arc(uiarc.dev,shadcn 注册表,组件源码在 `web/src/components/arc/`),`npm run build` 产出 `web/dist/` 由后端托管 |
| `local/` | 维护者的个人开发辅助工具,不入库。存在时先读 `local/AGENTS.md` |

## Python 环境(uv)

- 只用 uv 管理的 Python(`[tool.uv] python-preference = "only-managed"`),版本固定在 `.python-version`(`3.12.13`)。用 `uv sync` 建环境,`uv run` 运行。
- **为什么固定到补丁号**:uv 会建一个次版本号目录联接(如 `cpython-3.12-windows-x86_64-none`)指向真实安装目录,而 Windows 上管理员进程穿不过普通用户建的联接(os error 448)。agent 需要以管理员身份运行(游戏是提权进程),固定补丁号时 `.venv` 直接指向真实目录,不受影响。升级 Python 时改 `.python-version` 里的完整版本号。
- 依赖随功能逐步加入,完整清单见设计文档第 15 节。

## 在 Windows 上运行与调试

- 截图、键鼠、实机对局只能在 Windows 上验证;知识库、LLM 接入、决策层、感知算法(对截图 fixtures)可以在任何平台开发和测试。
- 在其他电脑上开发时:提交推送后在 Windows 上 `git pull`,再通过 SSH 运行。
- **SSH 会话看不到用户桌面**:在 SSH 里直接启动的进程截图是黑的、键鼠无效。需要接触游戏的进程要启动到用户已登录的桌面会话里,例如用计划任务:`schtasks /Create /TN <名字> /TR "<命令>" /SC ONCE /ST 00:00 /RL HIGHEST /IT /F`,再 `schtasks /Run /TN <名字>`。之后通过日志文件、`runs/` 目录和后端接口观察。
- Windows 的 OpenSSH 默认 shell 是 `cmd.exe`,输出是 GBK,命令前加 `chcp 65001 >nul &` 可改成 UTF-8。复杂的 PowerShell 用 `-EncodedCommand`(UTF-16LE 的 base64)传,避开引号转义。
- 游戏需为窗口模式、关闭 HDR;运行期间 Windows 不能锁屏、显示器不能休眠。

## 约定

- 文档、注释、界面文字用中文。
- `.bat` 文件保持纯 ASCII + CRLF(`.gitattributes` 已设置)。
- 设计文档区分"已实测"和"待核实";待核实的游戏规则数值不要写进代码或规则卡。
- 汇报进度时区分"已在非 Windows 平台验证"和"已在 Windows 实机验证"。
- 不要在游戏里做不可逆的操作(消耗资源、抽卡、购买等),除非明确要求。

## 前端约定

- 组件库是 Arc(https://uiarc.dev,不是 arcui.dev):组件源码通过 shadcn 注册表复制进 `web/src/components/arc/`,
  新增组件用 `cd web && npx shadcn@latest add @uiarc/<名称>`(注册表配置在 `web/components.json`)。
  组件目录:https://uiarc.dev/llms.txt;写法规范:https://uiarc.dev/r/skills/arc/SKILL.md 及同目录的 design.md、composition.md、checklist.md。
- 用法以组件源码里导出的 Props 类型为准;不要改 `components/arc/` 里的组件内部,布局写在各页面的 CSS Modules 里。
- 规范要点:只用语义 CSS 变量(`--surface`、`--border`、`--text-secondary`、`--accent` 等);字重只用 400/500;不用全大写、眉标、渐变和发光;
  每个页面只有一个主按钮(`Button` 默认是 primary,其他按钮显式写 `variant`);前台操作在原地确认,提示条只用于后台任务。
- 深色主题、紫色强调色:`index.html` 的 `<html data-theme="dark" data-accent="violet">`。只考虑电脑端,不做手机适配。
- 共用布局在 `web/src/lib/page.module.css`(页面容器、区块、成组的行)。
- 开发:`uv run cwagent serve` 起后端,`cd web && npm run dev` 起 Vite(代理 `/api`、`/templates` 到 8770)。
