# 待办

做完一条就删掉。跨平台能做的、只能在 Windows 实机验证的分开标。
机制上还没有定论的问题记在 `docs/OPEN_QUESTIONS.md`，不写在这里。

## 2026-11-16 优化方案（设计文档第 20 节）

### P0 程序缺陷 + 报表（跨平台写，Windows 实机验收：A8-1 不暂停打完整局）

已写完、在非 Windows 平台测过（`tests/test_p0.py`）：记忆线索带节点类型、出战人数未满时拦下、买经验后上场上限跟着等级变
（读不出等级就标过期、不拦上场）、首领节点程序打开「选择主角」（模型点「选择主角」「出战」前先收起商店）、
投资环境不再因「轮岗」说明里的「备战阶段」被认成备战、逐节点报表（`runs/<id>/report.md`、`cwagent runs report`）。

- [ ] Windows 实机验证上面这些：人数未满的「取消」「确认」位置（读不到文字时的兜底坐标是猜的）、「选择主角」按钮能点开、
  买经验前后读得到等级（Windows 实机）
- [ ] Windows 实机复测商店读取（31e66be、01c07e6 之后）（Windows 实机）
- [ ] A8-1 跑 3~5 整局拿基线，用报表对比（Windows 实机，等 Claude Code 接入合入后开跑）

### P1 输入分块 + 局面要点 + 规则卡

已做：攻略卡片按阶段给全（作者原文不再截到 160 字，有的写了逐节点打法；前期 / 中期带前后台、星级和羁绊人数；最终阵容带次选装备和推荐光环；
两组推荐投资策略分开）。入库格式变了，Windows 上 pull 后要跑一次 `cwagent knowledge rebuild`（不联网，用库里存的接口原始响应重建）。

- [ ] 「整局已知事实」块：开局、位面节点与词条、投资策略、主角光环、道具、经济修正，只追加
- [ ] 条目说明只在第一次出现时给；操作说明移进系统提示词；去掉整屏 OCR 列表、每回合的候选攻略和相关经验、程序的催促话
- [ ] `run_notes` 升级成分节的模型笔记
- [ ] 局面要点：利息与闲置金币、升级差几次经验、同名张数与「买了就升星」、羁绊档位、前台人数、相对方向的角色分类、战后异常
- [ ] 规则卡与打法原则拆开、去重、补齐（前后台分工、输一场的代价、攻略节奏标签）；整理维护者关于怪物词条影响的说明进记忆

### P2 多模态

- [ ] `Message` 带图片；OpenAI（tool 消息后跟一条带图的 user 消息）、Anthropic、JSON 模式三条路径 + 假 HTTP 单测
- [ ] 连通性测试改成读随机数字小图
- [ ] 截图标号（备战格子、商店、装备栏、道具栏；其他界面的文字和图标），对旧截图测试
- [ ] 决策带截图；`click(mark)`、`zoom`、`inspect`、`perception_issues`
- [ ] `runs/<id>/vision/` 存图、回放重发、网页显示；MCP 返回图片（等 Claude Code 接入合入后）

### P3 循环与动作

- [ ] 战略检查点；攻略默认固定一篇，检查点才比较
- [ ] `roll_until`、`level_to`、按目标装备合成；出战可和其他操作一起发；轮数上限改安全阀、刷新不打断回合
- [ ] 没见过的界面卡住就暂停交给人

### P4 道具栏

- [ ] 见下方「右上角道具栏」条目（需要 Windows 截图）

### P5 复盘

- [ ] 局后复盘（设计文档 5.4）、`perception_issues` 统计

## 感知（M1）

- [ ] **右上角道具栏（消耗品）的识别与使用** — 现在完全没有实现

  现状：道具名、数量角标、可用状态都不读。`src/cwagent/vision/equipment.py` 从 `ROW_Y = 248` 起只认装备格，
  注释写明"最上面一行是两个带数量角标的道具（不是装备，跳过）"；`config/regions.yaml` 没有道具栏区域；
  `src/cwagent/brain/screen_agent.py` 的动作枚举里没有消费道具栏道具的类型；知识库没有道具条目
  （官方接口 config 只有 role_list / trait_info_list / equipment_list / fight_augment_list / portal_list 等，没有道具表）。

  已核实的机制（游戏内教学文案与 wiki，2026-11-16 查）：

  | 中文名 | 英文名 | 用法（拖放目标） |
  |---|---|---|
  | 拆装扳手 | Disassembly Wrench | 拖到角色上卸下其全部装备；用后消失 |
  | 精密拆装扳手 | Precision Disassembly Wrench | 同上，可无限次使用；持有它时再获得拆装扳手变成 1 金币 |
  | 冶金炉 | Smelting Furnace | 拖到装备上重掷为同类型随机装备；拖到角色上卸下全部装备并重掷；用后消失 |
  | 特权赋予卡 | Special Privilege Card | 拖到进阶装备上转为同名特权装备；拖到角色上则选其身上一件进阶装备；用后消失 |
  | 员工投影仪 | Staff Projector | 拖到 3 费及以下角色上，在备战区生成 1 星复制品；用后消失 |
  | 完美投影仪 | Perfect Projector | 拖到任意角色上，在备战区生成 1 星复制品；用后消失 |
  | 好运令牌 | Good Fortune Token | 拖到角色上，从其 4 件推荐进阶装备中选 1 件获得；用后消失 |

  冶金炉那行与 S2 投资策略文案的分歧已经澄清：那条文案是**那条投资策略的特殊作用**，不是冶金炉的规则（维护者告知，2026-11-16）。

  来源：Fandom 英文 wiki [Equipment → Consumables](https://honkai-star-rail.fandom.com/wiki/Currency_Wars:_Zero-Sum_Game/Equipment#Consumables)
  （2026-08-08，S1）与 [Tutorial/Consumables 各页](https://honkai-star-rail.fandom.com/wiki/Tutorial/Consumables)（游戏内教学原文，2026-07-22）；
  用法在 S2 仍在：S2 [Startup Guide](https://honkai-star-rail.fandom.com/wiki/Currency_Wars/Startup_Guide)（2026-11-14）的任务条件写着
  "用精密拆装扳手拆装 10 次""使用冶金炉 3 次""用特权赋予卡升级进阶装备 2 次""用员工投影仪或完美投影仪复制角色 4 次"。
  中文 BWIKI 的[货币战争页](https://wiki.biligame.com/sr/%E8%B4%A7%E5%B8%81%E6%88%98%E4%BA%89)只有一句"已穿戴的装备可以使用拆装扳手拆取"，没有道具表。

  三条判断：

  1. 交互统一是**拖放**（道具 → 角色 / 道具 → 装备），不是点菜单。做一条"从道具槽拖到目标"的通路能覆盖全部七种。
  2. 投影仪要求**备战席有空位**（复制品进备战区）；员工投影仪限 3 费及以下，完美投影仪不限。
  3. 目标合法性各不相同：特权赋予卡只吃进阶装备；冶金炉拖角色是"全卸重掷"、拖装备是"单件重掷"。

  缺口（按实现顺序）：

  1. 区域：道具栏坐标。可先借 M7A 的 `(1540, 111, 1890, 206)`，见 `local/ref/March7thAssistant/tasks/weekly/currency_wars_ui.py:1112`
     的 `disassembly_tool()`（两个 50×50 模板、阈值 0.82，单槽中心约 `(1844, 163)`），实机校准后写进 `config/regions.yaml`。
  2. 图标模板：官方接口没有道具数据，`data/templates/` 下只有 equipment/augments/roles/cards/avatars/traits/spotlights/portals。
     模板要么从实机截图裁，要么从 wiki 的 50px 图标取（`Equipment <名字>.png`，够做模板匹配的起点）。
  3. 数量角标 OCR（多位数）与不可用/灰状态。
  4. 动作：`use_item`（道具槽 + 目标 `zone/index` 或装备编号），照 `compose`/`equip` 的"动作前校验 + 动作后校验 + 回滚"写法。
  5. 档案：把"道具栏（名字×数量）"写进 `brain/ledger.py` 的当前状态，让模型决定何时用。
  6. 规则卡：`knowledge/rules.md` 补上面那张表（标来源），冲突项标【待核实】。已写入：扳手消耗规则、冶金炉重掷（维护者告知，2026-11-16）；
     投影仪、特权赋予卡、好运令牌仍是【待核实】，要实机核对后才知道能不能去掉标记。
  7. 测试：实机截图 fixture 做识别测试（仓库现在没有一张这些道具的实机截图）。

  前置条件：Windows 上抓几张 S2 备战界面截图（带道具的、道具被点开/拖拽时的、数量角标清楚的）。

  参考：trail-cli 的 `EquipmentGridProfile` 里声明了 `tracked_top_slot_count = 2` / `tracked_top_slots_excluded = True`
  （`local/ref/trail-cli/trail/scenes/cw/equipment_grid.py:12`），但遍历时没消费这两个字段；M7A 只实现了拆装扳手一种，
  做法是把角色拖到扳手图标上卸装备（`local/ref/March7thAssistant/tasks/weekly/currency_wars.py:1391` 的 `_disassemble_for_upgrade`）。
