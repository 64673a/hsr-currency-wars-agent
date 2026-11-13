# hsr-currency-wars-agent

让 LLM 自主游玩《崩坏:星穹铁道》常驻玩法「货币战争」S2「入画奇航」的 agent:看屏幕截图理解局面,由模型决定买卖、站位、装备和各种三选一,再操作键鼠执行。目标是能看到模型每一步怎么想、尽量提高胜率,并能对比不同模型(兼容 OpenAI 和 Anthropic 两种接口格式)。

> **状态:设计阶段。** 目前仓库只有设计文档和项目骨架,还不能运行。

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

## 许可证

[GPL-3.0-only](LICENSE)
