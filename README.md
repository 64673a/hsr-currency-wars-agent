# hsr-currency-wars-agent

让 LLM 自主游玩《崩坏:星穹铁道》常驻玩法「货币战争」S2「入画奇航」的 agent:看屏幕截图理解局面,由模型决定买卖、站位、装备和各种三选一,再操作键鼠执行。目标是能看到模型每一步怎么想、尽量提高胜率,并能对比不同模型(兼容 OpenAI 和 Anthropic 两种接口格式)。

> **状态:开发中。** 已能在 Windows 上自动打完整局,识别和决策还在持续改进。

## 风险声明

第三方自动化工具违反米哈游用户协议,存在封号风险。请只在小号上使用,后果自负。

## 设计

完整设计见 [docs/TECH_DESIGN.md](docs/TECH_DESIGN.md):数据来源(米游社官方阵容工具接口)、感知(OCR + 模板匹配)、决策(LLM 战略层 / 战术层 + 规则校验)、执行、LLM 接入、观战前端和评测。

## 运行环境

- Windows 10(2004 及以上)或 Windows 11,星穹铁道 PC 客户端(国服),1920x1080 窗口模式,关闭 HDR
- agent 要以**管理员身份**运行(游戏是提权进程,普通权限发不了键鼠)
- 运行期间不能锁屏,显示器不能休眠

### 需要的第三方程序

| 程序 | 用途 | 安装方式 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | 管理 Python 和依赖(版本固定在 `.python-version`) | 手动安装 |
| [Node.js](https://nodejs.org/) 20+ | 构建网页界面 | 手动安装,只在构建时用到 |
| [ffmpeg](https://github.com/BtbN/FFmpeg-Builds) 8.1(BtbN 的 GPL 构建) | 实时画面:抓游戏窗口、用显卡编码 | `cwagent serve` 第一次启动时自动下载,也可以手动安装(见下文) |
| [MediaMTX](https://github.com/bluenviron/mediamtx) v1.21.3 | 实时画面:WebRTC 推流 | 同上 |

Python 依赖(包括 OCR 模型)由 `uv sync` 安装。

```bash
git clone https://github.com/64673a/hsr-currency-wars-agent.git
cd hsr-currency-wars-agent
uv sync
```

## 接入模型

不绑定任何厂商。任何兼容 OpenAI Chat Completions 或 Anthropic Messages 格式的接口都可以接入:先添加厂商(地址 + key),再从它的模型列表里选模型。

```bash
uv run cwagent providers add my-provider -f openai -u https://api.example.com/v1 -k <API key>
uv run cwagent providers models my-provider     # 查看可用模型
uv run cwagent models add my-provider           # 选择模型,建模型档案
uv run cwagent models test                      # 连通性测试
```

配置保存在 `config.yaml`(不入库),格式见 [config.example.yaml](config.example.yaml)。

## 网页界面

```bash
cd web && npm install && npm run build && cd ..   # 需要 Node.js 20+
uv run cwagent knowledge sync                     # 从米游社官方接口同步角色、羁绊、装备、阵容等(约 1 分钟)
uv run cwagent serve                              # 打开 http://127.0.0.1:8770
```

有「对局」(开始 / 停止对局、游戏实时画面、逐步回看截图和模型的判断)、「知识库」(官方数据、指引卡、记忆)和「设置」(厂商、模型档案、决策层、运行参数)三个页面。

要从局域网里的其他设备(手机、另一台电脑)打开网页,把 `config.yaml` 里的 `server.host` 改成 `0.0.0.0`,并在防火墙里放行 8770 端口。网页没有登录机制,只在可信的局域网里这样做。

### 实时画面

对局页可以看游戏的实时画面(1080p、最高 60fps,局域网内低延迟)。原理是 ffmpeg 抓游戏窗口(窗口被其他窗口挡住也能抓,但不能最小化)、用显卡编码,经 MediaMTX 以 WebRTC 推给浏览器;没人观看时不抓取、不编码。

- **自动安装**:`cwagent serve` 启动时如果缺少 ffmpeg 或 MediaMTX,会在后台从上面表格里的官方发布页下载到 `data/tools/`(约 200 MB),下载期间网页照常可用,装好后实时画面自动出现。以管理员身份运行时会同时添加防火墙规则(只放行本地子网访问 UDP 8189)。
- **手动安装**(GitHub 下载不了时):
  1. 下载 [mediamtx_v1.21.3_windows_amd64.zip](https://github.com/bluenviron/mediamtx/releases/download/v1.21.3/mediamtx_v1.21.3_windows_amd64.zip),把其中的 `mediamtx.exe` 放到 `data/tools/mediamtx/mediamtx.exe`。版本要一致,MediaMTX 的配置格式随版本变化;
  2. 下载 [ffmpeg-n8.1-latest-win64-gpl-8.1.zip](https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip),把其中的 `bin/ffmpeg.exe` 放到 `data/tools/ffmpeg/ffmpeg.exe`。其他来源的 ffmpeg 也可以,只要版本不低于 8.1、带 `gfxcapture`(`ffmpeg -filters` 里能看到);
  3. 以管理员身份运行 `uv run cwagent stream setup`,检查文件并添加防火墙规则。
- **编码器**:自动选择 NVIDIA(NVENC)或 AMD(AMF)的显卡编码;都不可用时改用 CPU 编码(占用明显更高,可以在 `config.yaml` 里调低 `stream.fps`)。
- **不需要实时画面**:`config.yaml` 里设 `stream.enabled: false`,不会下载也不会启动。
- **从其他设备看不到画面**:多半是防火墙拦了 UDP 8189。以管理员身份运行 `uv run cwagent stream setup`,或者手动放行 `data/tools/mediamtx/mediamtx.exe` 的入站 UDP 8189。

## 许可证

[GPL-3.0-only](LICENSE)
