# AGENTS.md

## 项目

- 让 LLM 自主游玩《崩坏:星穹铁道》货币战争 S2「入画奇航」的 agent。
- 权威设计文档:`docs/TECH_DESIGN.md`。开始任何工作前先读它;第 1.6 节是已确定的设计决策,不要擅自改。
- 现状:正在做第一个里程碑(M0)。已完成:配置管理(厂商 + 模型档案两层)、LLM 接入层(两种格式、模型列表、连通性测试)、知识同步(`cwagent knowledge sync`)、截图模块(`capture/`)。待做:`collect`、`regions check`、后端、前端。
- 流程不能写死:投资环境、投资策略、奇遇伙伴都会改变节点结构和出现的界面(设计文档 2.3)。奇遇伙伴数据官方接口没有,人工整理在 `knowledge/companions.yaml`。
- 运行形态:agent 作为一个服务运行在装有游戏的 Windows 上,像 March7thAssistant 一样直接定位游戏窗口截图、发送键鼠。

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 设计文档 |
| `src/cwagent/` | agent 本体(按设计文档第 15 节的结构逐步实现) |
| `pyproject.toml`、`uv.lock`、`.python-version` | uv 项目配置 |
| `config.example.yaml` | 配置示例,复制为 `config.yaml`(不入库)后使用 |
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
