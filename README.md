# hsr-currency-wars-agent

让 LLM 自主游玩《崩坏:星穹铁道》常驻玩法「货币战争」S2「入画奇航」的 agent:看屏幕截图理解局面,由模型决定买卖、站位、装备和各种三选一,再操作键鼠执行。目标是能看到模型每一步怎么想、尽量提高胜率,并能对比不同模型(兼容 OpenAI 和 Anthropic 两种接口格式)。

> **状态:开发中。** 已完成模型接入、知识库、记忆系统和网页界面,还不能玩游戏。

## 风险声明

第三方自动化工具违反米哈游用户协议,存在封号风险。请只在小号上使用,后果自负。

## 设计

完整设计见 [docs/TECH_DESIGN.md](docs/TECH_DESIGN.md):数据来源(米游社官方阵容工具接口)、感知(OCR + 模板匹配)、决策(LLM 战略层 / 战术层 + 规则校验)、执行、LLM 接入、观战前端和评测。

## 运行环境(规划)

- Windows + 星穹铁道 PC 客户端(国服),1920x1080 窗口模式,关闭 HDR
- [uv](https://docs.astral.sh/uv/) 管理 Python(版本固定在 `.python-version`)

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

目前有「知识库」(浏览官方数据、指引卡、记忆)和「设置」(厂商、模型档案、决策层、运行参数)两个页面。

## 许可证

[GPL-3.0-only](LICENSE)
