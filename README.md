# 货币战争 Agent(hsr-currency-wars-agent)

让大模型自主游玩《崩坏:星穹铁道》常驻玩法「货币战争」S2「入画奇航」的 agent。它看游戏截图理解局面,由模型决定买卖、站位、装备、升级、刷新和各种三选一,再用键鼠操作游戏,一直打到结算。每一步的截图、交给模型的局面、模型的思考和操作都能在网页上实时查看和回放。

> **开发中。** 已能在 Windows 上自动打完整局,识别和决策还在持续改进,胜率不做保证。

> **风险提示:** 第三方自动化工具违反米哈游用户协议,存在封号风险。请只在小号上使用,后果自负。

## 功能

- **自主打完整局**:从游戏大世界或货币战争首页出发,自己进入标准博弈、选难度、开局,打完结算后可以接着开下一局。
- **决策过程看得见**:网页上逐步显示截图、模型看到的局面、思考过程(流式)和执行的操作;打完的对局可以逐步回放,并生成逐节点报表。
- **不绑定模型厂商**:兼容 OpenAI Chat Completions 和 Anthropic Messages 两种接口格式,官方接口、中转网关、本地部署都可以接。每局可以选模型和思考强度,方便对比不同模型。
- **用自己的 agent 工具下棋**:内置 MCP 服务,Claude Code、Codex、DSH、Antigravity 等工具可以连上来,用它们自己的模型和额度来做决策。
- **官方数据和攻略**:从米游社官方阵容工具同步角色、羁绊、装备、投资策略、投资环境、主角光环和热门阵容攻略,给模型作参考。
- **经验记忆**:游戏经验整理成一条条记忆,对局中按局面把相关的几条提示给模型;自己也可以添加。
- **实时画面**:在浏览器里看游戏的实时画面(局域网内低延迟)。

## 运行要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10(2004 及以上)或 Windows 11 |
| 游戏 | 《崩坏:星穹铁道》PC 客户端(国服),简体中文 |
| 画面 | **窗口模式、1920x1080**,关闭 HDR |
| 权限 | agent 以**管理员身份**运行(游戏是提权进程,普通权限发不了键鼠) |
| 运行期间 | 不能锁屏,显示器不能休眠,游戏窗口不能最小化;不要同时操作鼠标键盘 |
| 模型 | 支持图片输入;最好支持工具调用(不支持时可改用 JSON 输出模式) |
| 玩法 | 只支持货币战争 S2「入画奇航」的**标准博弈**,不支持超频博弈 |
| 账号 | 货币战争的「优势布局」全部激活。agent 按全部激活理解规则(如保险机制、战斗后回复小队生命),没点满时它对血量和金币的判断会有偏差;打到 A8 时一般都已点满 |

实时画面需要显卡编码(NVIDIA NVENC 或 AMD AMF),没有时自动改用 CPU 编码,占用会明显变高。

## 安装

### 便携版(推荐)

1. 在 [Releases](https://github.com/64673a/hsr-currency-wars-agent/releases) 下载最新的 `hsr-cw-agent-windows-x64-v*.zip`。
2. 解压到一个固定的文件夹(路径里最好不要有中文和空格)。
3. 双击 `start.bat`。

包里已经内置 Python、全部依赖、网页界面、ffmpeg、MediaMTX 和 OCR 模型,不需要另外安装任何东西。第一次运行会请求管理员权限、从官方接口下载游戏数据(约 1 分钟)、安装开晶矿用的虚拟手柄驱动,然后在浏览器打开 http://127.0.0.1:8770 。

便携版按版本发布,可能比源码旧;想用最新的改进请从源码运行。

### 从源码运行

需要先装 [Git](https://git-scm.com/) 和 [uv](https://docs.astral.sh/uv/)(uv 会自动下载项目固定版本的 Python,不需要自己装)。

```powershell
git clone https://github.com/64673a/hsr-currency-wars-agent.git
cd hsr-currency-wars-agent
uv sync                           # 安装依赖(含 OCR 模型)
uv run cwagent knowledge sync     # 从米游社官方接口同步游戏数据和攻略(约 1 分钟)
uv run cwagent serve              # 启动,浏览器打开 http://127.0.0.1:8770
```

`cwagent serve` 要在**管理员权限**的终端里运行。第一次启动时会自动下载网页界面、实时画面用的 ffmpeg 和 MediaMTX,并安装虚拟手柄驱动,下载期间网页照常可用。

更新:

```powershell
git pull
uv sync
uv run cwagent web download       # 换成最新的网页界面
```

## 快速开始

### 1. 接入模型

在网页「设置」页添加厂商(接口格式、地址、API key),从它的模型列表里勾选模型建成模型档案,再点连通性测试,确认能看图、能调用工具。

也可以用命令行:

```powershell
uv run cwagent providers add my-provider -f openai -u https://api.example.com/v1 -k <API key>
uv run cwagent providers models my-provider     # 查看可用模型
uv run cwagent models add my-provider           # 选择模型,建模型档案
uv run cwagent models test                      # 连通性测试
```

配置保存在程序目录的 `config.yaml`(含 API key,不要分享给别人),格式见 [config.example.yaml](config.example.yaml)。设置页还可以给不同的决策环节指定不同的模型,例如界面导航用便宜的快模型。

### 2. 准备游戏

启动游戏,调成窗口模式、1920x1080、关闭 HDR。停在大世界、货币战争首页都可以,agent 会自己导航进去;也可以接着一局没打完的标准博弈。

开拓者的名字是玩家自己取的,商店和角色详情里显示的是这个名字。在设置页填上它,agent 才能认出开拓者。

### 3. 开始对局

在「对局」页选模型、思考强度、难度和局数,点开始。难度写成职级加等级,如 `A8-1`。

对局开始后,页面左边是实时画面和步骤列表,右边是当前这一步的判断。模型还在思考时,这一步就会出现,思考和判断边生成边显示。

### 4. 停止

- 对局页点「停止」;
- 或按停止快捷键 `Ctrl+Alt+F10`(被其他程序占用时会换成别的组合,以对局页显示的为准);
- 或把鼠标甩到屏幕任意一个角落,立即急停。

遇到无法判断的情况(难度没能调好、连接中断、同一节点反复卡住等),agent 会暂停并保留现场,等你处理。

## 用自己的 agent 工具下棋(MCP)

`cwagent serve` 在 `http://127.0.0.1:8770/mcp` 提供 MCP 服务。支持 MCP 的 agent 工具连上来以后,用它自己的模型和额度做决策,不需要在这里配置 API key。截图、识别和操作仍由 cwagent 完成,网页照常显示每一步。

### Claude Code

在 Claude Code 里安装插件:

```
/plugin install cwagent --marketplace 64673a/hsr-currency-wars-agent
```

旧版本的 Claude Code 不支持这个写法,分两步:

```
/plugin marketplace add 64673a/hsr-currency-wars-agent
/plugin install cwagent@hsr-currency-wars-agent
```

安装时会问 cwagent 地址:Claude Code 和游戏在同一台电脑上,用默认的 `http://127.0.0.1:8770`。然后开始:

```
/cwagent:play A8-1
```

难度可以不写,不写就用设置页里的。对局没结束时它想停下来,插件会让它接着下;中途想停,直接告诉它,或在网页上点停止。详见 [integrations/claude-code](integrations/claude-code/README.md)。

### Codex、DSH、Antigravity

- Codex:见 [integrations/codex](integrations/codex/README.md)
- DSH:见 [integrations/dsh](integrations/dsh/README.md)
- Antigravity:见 [integrations/antigravity](integrations/antigravity/README.md)

也可以先在网页对局页把模型选成「外部 agent(MCP)」点开始,再让 agent 工具接手。局面交出去后 15 分钟没有任何动静,对局会自动暂停。

## 网页界面

| 页面 | 内容 |
|---|---|
| 对局 | 开始 / 停止对局,游戏实时画面,当前这一局逐步的截图、局面和模型的判断 |
| 对局记录 | 以前的对局:结果、到达的节点、步数;逐步回放,删除记录 |
| 知识库 | 官方数据(角色、羁绊、装备、投资策略、投资环境、主角光环)、攻略、奇遇伙伴、敌人词条、记忆的浏览和审核 |
| 设置 | 厂商、模型档案、各决策环节用哪个模型、运行参数 |

### 从局域网里的其他设备打开

把 `config.yaml` 里的 `server.host` 改成 `0.0.0.0`,并在 Windows 防火墙里放行 TCP 8770 端口,然后用 `http://<这台电脑的局域网 IP>:8770` 打开。网页没有登录机制,只在可信的局域网里这样做。

### 实时画面

对局页可以看游戏的实时画面(1080p,最高 60fps)。ffmpeg 抓游戏窗口(被其他窗口挡住也能抓,但不能最小化)、用显卡编码,经 MediaMTX 以 WebRTC 推给浏览器;没人观看时不抓取、不编码。

- **自动安装**:`cwagent serve` 启动时缺少 ffmpeg 或 MediaMTX,会在后台下载到 `data/tools/`(约 200 MB),装好后实时画面自动出现。以管理员身份运行时会同时添加防火墙规则(只放行本地子网访问 UDP 8189)。
- **手动安装**(下载不了 GitHub 时):
  1. 下载 [mediamtx_v1.21.3_windows_amd64.zip](https://github.com/bluenviron/mediamtx/releases/download/v1.21.3/mediamtx_v1.21.3_windows_amd64.zip),把其中的 `mediamtx.exe` 放到 `data/tools/mediamtx/mediamtx.exe`。版本要一致,MediaMTX 的配置格式随版本变化;
  2. 下载 [ffmpeg-n8.1-latest-win64-gpl-8.1.zip](https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip),把其中的 `bin/ffmpeg.exe` 放到 `data/tools/ffmpeg/ffmpeg.exe`。其他来源的 ffmpeg 也可以,只要版本不低于 8.1、带 `gfxcapture`(`ffmpeg -filters` 里能看到);
  3. 以管理员身份运行 `uv run cwagent stream setup`,检查文件并添加防火墙规则。
- **编码器**:自动选择 NVIDIA(NVENC)或 AMD(AMF)的显卡编码,都不可用时改用 CPU 编码(可以在 `config.yaml` 里调低 `stream.fps`)。
- **不需要实时画面**:`config.yaml` 里设 `stream.enabled: false`,不会下载也不会启动。

## 知识库、攻略和记忆

- **游戏数据**:`cwagent knowledge sync` 从米游社官方阵容工具的接口同步,赛季更新后重新同步一次即可(网页知识库页也有同步按钮)。
- **攻略**:同步时按热度抓取官方阵容攻略,只留有游戏内使用次数的,和收藏不少于 200 且写全了说明、推荐投资策略、投资环境的;最终阵容完全相同的只留一篇。整个赛季过期的攻略不入库,本期过期的会标出来。攻略只给模型作方向参考,买谁、怎么站、给谁装备由模型自己判断。设置里可以关掉攻略,看模型不靠攻略时的水平。
- **记忆**:项目维护者整理的公共经验在 [knowledge/memories.yaml](knowledge/memories.yaml),随程序更新;自己的经验可以在知识库页或用 `cwagent memory add` 添加,采纳后生效。对局中会按当前的角色、节点和界面,把相关的几条提示给模型。对局后自动复盘生成记忆还在开发中。
- **奇遇伙伴、敌人词条**:官方接口没有这些数据,对局中遇到一个记一个。

## 命令行

日常使用网页就够了,下面是命令行能做的事(从源码运行时在前面加 `uv run`):

| 命令 | 用途 |
|---|---|
| `cwagent serve` | 启动后端和网页界面 |
| `cwagent play` | 不开网页,直接在终端里打一局(`--model` 选模型档案,`--effort` 选思考强度) |
| `cwagent knowledge sync` / `stats` | 同步游戏数据 / 查看本地数据的版本和条目数 |
| `cwagent providers` / `models` | 管理厂商和模型档案 |
| `cwagent memory list` / `add` / `approve` | 查看、添加和采纳记忆 |
| `cwagent runs report [对局]` | 生成逐节点报表(写到对局目录的 `report.md`,默认最近一局) |
| `cwagent replay <对局目录> --models a,b` | 把一局里的历史局面原样交给其他模型,只比较回答、不操作游戏 |
| `cwagent web download` | 下载最新的网页界面 |
| `cwagent stream setup` | 安装实时画面用的程序并放行防火墙 |
| `cwagent uninstall` | 撤销对系统的改动(见下文) |

每个命令加 `--help` 查看完整参数。

## 文件位置

都在程序目录下:

| 路径 | 内容 |
|---|---|
| `config.yaml` | 厂商(含 API key)、模型档案和运行参数 |
| `data/knowledge.sqlite` | 同步的游戏数据和攻略 |
| `data/memory.sqlite` | 本机积累的记忆 |
| `data/tools/` | 自动下载的 ffmpeg、MediaMTX |
| `runs/<时间>_<模型>/` | 每局的记录:逐步记录 `steps.jsonl`、截图、发给模型的请求、报表 `report.md` |

## 常见问题

**键鼠操作没反应。** agent 没有以管理员身份运行。便携版用 `start.bat` 启动;从源码运行时在管理员终端里运行 `cwagent serve`。

**界面识别错乱、找不到按钮。** 检查游戏是不是窗口模式 1920x1080、关闭了 HDR、语言是简体中文。显卡驱动或其他软件在游戏画面上叠加的帧数、性能监控最好关掉。

**网页打开后界面不正常。** 更新代码后网页界面没跟上,运行 `uv run cwagent web download`。

**其他设备上看不到实时画面。** 多半是防火墙拦了 UDP 8189。以管理员身份运行 `uv run cwagent stream setup`,或者手动放行 `data/tools/mediamtx/mediamtx.exe` 的入站 UDP 8189。有虚拟网卡(VPN、加速器)时,在 `config.yaml` 的 `stream.hosts` 里填局域网 IP。

**对局停在某个界面不动。** 看对局页最后一步的说明:暂停时会写原因。手动处理好以后重新开始对局,agent 会接着这一局打。

## 卸载

便携版双击 `uninstall.bat`:卸载 cwagent 装的虚拟手柄驱动、删除防火墙规则,再删除整个文件夹(含配置、知识库和对局记录)。

从源码运行时,以管理员身份:

```powershell
uv run cwagent uninstall          # 撤销对系统的改动:卸载 cwagent 装的虚拟手柄驱动、删除防火墙规则和下载的程序;数据保留
uv run cwagent uninstall --all    # 连同知识库、对局记录、配置(含 API key)、网页界面一起删除
```

- 虚拟手柄驱动只有在是 cwagent 装的时候才卸载,电脑上原来就有的(其他软件装的)不动。
- `--all` 之后删掉整个程序文件夹就清理干净了。uv 管理的 Python 是各项目共用的,不会删除,确实不再需要时运行 `uv python uninstall 3.12.13`。

## 参与开发

- 设计文档:[docs/TECH_DESIGN.md](docs/TECH_DESIGN.md)(数据来源、感知、决策、执行、模型接入、网页和评测),开发约定见 [AGENTS.md](AGENTS.md)。
- 测试:`uv run pytest`。识别算法、知识库、模型接入和决策逻辑的测试用截图样本和假接口,不需要开游戏。
- 网页界面:React + Vite,源码在 `web/`。改了以后运行 `uv run cwagent web build` 重新构建(需要 Node.js 20+)。
- 截图、键鼠和实机对局只能在 Windows 上验证。

## 致谢

- [March7thAssistant](https://github.com/moesnow/March7thAssistant):开局导航用到了它的页面图素材和页面关系(GPL-3.0)。
- [trail-cli](https://github.com/jiwangyihao/trail-cli):S1 时期的货币战争实现,参考了其中的通用规则和备战思路。
- 游戏数据和阵容攻略来自米游社官方阵容工具。

## 许可证

[GPL-3.0-only](LICENSE)
