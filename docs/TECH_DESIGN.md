# 崩坏:星穹铁道「货币战争」S2 LLM 游玩 Agent — 技术设计文档

- 文档版本:v0.2(2026-11-14)
- 状态:设计定稿,未开始实现
- 取代:S1「零和博弈」时期的 v0.1 设计稿(未收录)
- 许可证:GPL-3.0-only

**给实现者的说明**:本文档是自包含的,你不需要读过之前的任何讨论。文中把事实分成两类:标注"已实测"的是 2026-11-14 实际调用接口或读源码得到的;标注"待核实"的来自第三方攻略或 S1 时期资料,**不要当成事实写进代码或规则卡**,需要在游戏内确认(清单见第 18 节)。遇到文档没覆盖的实现细节,自行按常规做法决定;遇到会改变架构或需要游戏内信息的问题,先问项目维护者。

---

## 1. 项目概述

### 1.1 目标

做一个由 LLM 自主游玩《崩坏:星穹铁道》常驻玩法「货币战争」S2 赛季「入画奇航」的 agent。三个诉求同等重要:

1. **好玩**:能看到模型每一步在想什么、为什么这么做;
2. **尽量高胜率**:从最低难度稳定通关开始,逐级提高难度;
3. **可对比模型**:任意模型都能接入,同一批局面下能并排比较不同模型的思考和决策。

### 1.2 非目标

- 不读游戏内存、不解析封包,只做"看屏幕 + 动键鼠"的外部自动化;
- 不做强化学习训练(没有模拟器);
- 不做多账号并发、打金等功能;
- 不做"副驾模式"(只给建议、人来点),首个可用形态就是全自动。

### 1.3 运行形态

- **运行环境**:Windows PC + 星穹铁道 PC 客户端(国服,简体中文),1920x1080 窗口模式,关闭 HDR,放在主显示器;对局期间键鼠被 agent 占用。agent 作为一个服务运行在这台 Windows 上,像 March7thAssistant 一样直接定位游戏窗口截图、发送键鼠,不经过任何中转。
- **开发方式**:代码可以在其他电脑上编辑,推送后在 Windows 上 `git pull`,再通过 SSH 运行和调试(注意事项见第 16 节)。
- **玩法特性**:自走棋类玩法,战斗自动进行,agent 只在备战、三选一等阶段决策。备战阶段是否有倒计时待核实;设计上按"单次决策允许十秒级延迟"处理。
- **观战**:Windows 被占用,所以网页界面要能在局域网内的另一台设备(电脑 / 手机)上打开。

### 1.4 合规与风险

第三方自动化违反米哈游用户协议,有封号风险,建议用小号。README 里要写明这一点。所用米游社接口是网页自身调用的非官方公开接口,必须低频访问(见 3.1.3)。

### 1.5 许可

GPL-3.0-only。这样可以直接参考或移植 March7thAssistant(GPL-3.0)的界面流程、区域坐标、卡死恢复逻辑。拷入代码时保留原版权声明;仓库根目录放 `LICENSE` 和 `NOTICE`。

### 1.6 已定的设计决策

以下是和用户确认过的,不要改:

| 议题 | 决定 | 原因 |
|---|---|---|
| 语言 | 后端 Python | OCR、截图、键鼠库都在 Python 生态 |
| 感知 | OCR + 模板匹配,不用视觉大模型提取局面 | 准确、便宜、可离线测试;决策模型只吃文本,非视觉模型也能参与对比 |
| 攻略的角色 | 攻略只给大方向和玩法指导;过渡阵容、装备选择、升本、D 牌(刷新商店)时机由模型自己判断 | 用户想看模型自己思考,而不是照抄脚本 |
| 决策粒度 | 语义原子动作为主,加少量宏 | 思考过程有看头,同时控制调用次数 |
| 模型接入 | 统一用 URL + API key 直连 HTTP,不集成厂商 SDK;同时支持 OpenAI 格式和 Anthropic 格式 | 用户要自由换模型和网关 |
| 前端 | 独立网页应用,用 ARC UI 组件库 | 用户指定 |
| 首个形态 | 全自动对局 | 用户指定 |

---

## 2. 玩法建模(S2)

### 2.1 玩法速览

玩家用金币在商店招募角色组建队伍,经过 3 个位面的一系列节点,每个节点自动战斗,击败最终首领即通关。

**已实测**(来自官方接口数据,见第 3 节):

- 角色费用为 1 / 2 / 3 / 4 / 5 费,另有 7 费的「星神★阿哈」(同名角色在数据中有 4 / 5 / 7 费三个形态);
- 角色分前台(Front)、后台(Back)、前后台通用(Common)三种站位类型,前后台技能不同;
- 角色有 1–3 星,技能描述按星级给出;
- 羁绊 34 个,分四类:阵营(12)、流派(11)、专属(6)、特殊(5);羁绊按上阵人数分档激活,例如「连击」为 2 / 4 / 6 / 8 人四档;
- 装备可合成,数据中有 105 件,类别为:进阶装备、特权装备、星徽、餐品改件、列车改件、宝钻、固有装备;简易装备两两合成进阶装备;
- 投资策略(对局中的三选一增益)298 条,三档品质;
- 投资环境 73 条(开局选择,影响整局);
- **主角光环**(S2 新机制)82 条:为某个上阵角色选择一个强化效果,每条带标签和适用角色列表。

**待核实**(第三方攻略或 S1 资料,数字不可直接使用):

- 小队生命初始 100,战斗未在行动值限制内清场会扣血,归零则对局结束;
- 最多上阵 10 人(4 前台 + 6 后台),上阵人数上限等于当前等级;备战席格数;
- 每个角色最多 3 件装备;
- 三个同名同星角色自动合成升一星;
- 存金币有利息且有上限,连胜有额外金币;
- 「奇遇」(S2 新机制):开局随机一个同行者,为整局提供增益,据称共 11 个、分装备 / 资源 / 节点三类,并新增「幕后茶歇」奖励点;
- 主角光环在位面 1、位面 2 的首领前各选一次;
- 阿哈通过收集和「欢愉」/「幻月游戏」羁绊升到 7 费,届时会改变商店内容;
- 通关时按剩余生命值提升职级(S1 规则:≥70 升 3 级,≥40 升 2 级,否则 1 级),失败不掉级;
- 有「标准博弈」和耗时更短的「超频博弈」两种模式,据称超频博弈没有利息;
- 商店各费用出现概率随等级变化(S1 时期的概率表见附录 C)。

### 2.2 对局流程与决策点

```
主页 → 选模式(标准/超频) → 选难度 → 开始对局
  → 投资环境选择 → (奇遇同行者揭示)
  → [ 备战阶段 → 自动战斗 → 结果 → 补给/投资策略三选一/其他事件 ] × N
       └ 位面1、位面2 首领前:主角光环选择
  → 通关或失败 → 结算、领奖
```

| 编号 | 决策点 | 频率 | 决策方 |
|---|---|---|---|
| D1 | 模式与难度 | 每局 1 次 | 配置 |
| D2 | 投资环境选择 | 每局 1 次 | LLM(选择类) |
| D3 | 对局方向制定 / 调整 | 每局 1 次 + 触发式 | LLM(战略层) |
| D4 | 商店:买、卖、刷新、买经验 | 每个备战阶段多次 | LLM(战术层) |
| D5 | 站位:前台 / 后台 / 备战席移动 | 每个备战阶段 | LLM(战术层) |
| D6 | 装备合成与穿戴 | 每个备战阶段 | LLM(战术层) |
| D7 | 投资策略三选一 | 对局中多次 | LLM(选择类) |
| D8 | 补给选择 | 对局中数次 | LLM(选择类) |
| D9 | 主角光环选择(选角色 + 选效果) | 每局约 2 次 | LLM(选择类) |
| D10 | 放弃 / 重开 | 触发式 | 规则 + 战略层 |
| D11 | 继续、跳过、领奖、已知弹窗 | 高频 | 规则(反射层) |

S1 的「专家顾问」交互:S2 接口中所有角色 `is_expert` 均为 false,先不做,待核实后决定是否彻底删除。

---

## 3. 数据源

### 3.1 官方阵容工具接口(主数据源,已实测为 S2 数据)

米游社「货币战争专题页」背后的接口,免登录可读。

#### 3.1.1 静态配置

`GET https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/config?game=hkrpg`

2026-11-14 实测返回 `retcode=0`,约 394 KB,`data` 内容:

| 字段 | 规模 | 内容 |
|---|---|---|
| `season_id` / `sub_season_id` / `rpg_game_big_version` | `"2"` / `"1"` / `"4.7"` | 赛季与版本,入库时记录,用于判断数据是否过期 |
| `role_list` | 63 | `id`、`name`、`rarity`(字符串,即费用:1–5 和 7)、`front_back_type`(Front / Back / Common)、`trait_details[]`(所属羁绊的 id、name、desc)、`skills[]`(按 Front / Back 分,各含 `skill_stars[]` 1–3 星描述和 `category_tags` 如 输出 / 辅助)、`is_hide`、`is_expert`、`icon`、`big_icon`。分布:1 费 14、2 费 12、3 费 12、4 费 13、5 费 11、7 费 1;Common 26、Front 21、Back 16;有 1 条 `is_hide=true`;「星神★阿哈」出现 3 条(rarity 4 / 5 / 7) |
| `equipment_list` | 105 | `id`、`name`、`desc`、`ability_name`、`priority`、`compose_list[]`(每项含 `craft_id` 和 `childrens[]` 两个原料,原料是完整的装备对象)、`category`、`category_name`、`properties[]`、`icon`。类别:进阶装备 36、特权装备 36、星徽 21、餐品改件 6、列车改件 4、宝钻 1、固有装备 1。原料里还会出现「简易装备」「钻石」等不在顶层列表的类别 |
| `fight_augment_list` | 298 | 投资策略:`id`、`name`、`desc`、`quality`(1 / 2 / 3,分别 76 / 113 / 109 条)、`icon` |
| `portal_list` | 73 | 投资环境:`id`、`title`、`desc`、`icon`。例:「火药味:开局时获得2个简易武装箱。」 |
| `spot_light_list` | 82 | 主角光环:`id`、`name`、`desc`、`tag_list[]`、`suitable_role_list[]`(角色 id)、`icon`。例:「蚁群效应:场上每有1个1费角色,该角色的前/后台强度提高45%,3星1费提供2份加成。」 |
| `spot_light_tag_list` | 51 | 主角光环标签(输出、生存、1费、升星、金币、刷新……) |
| `trait_info_list` | 34 | 羁绊:`trait_id`、`trait_name`、`trait_type`(0 阵营 / 1 流派 / 2 专属 / 3 特殊)。**`layers`、`role_ids`、`simple_desc` 在此接口中为空**,档位数据从阵容接口取(见 3.1.2),成员关系从 `role_list[].trait_details` 反推 |
| `role_property_list` | 57 | `property_type` → `name`,用于解析描述文本里的占位符 |
| `label_list` | 5 | 运营节奏标签:5级搜牌 / 6级搜牌 / 7级搜牌 / 速升8级 / 速升9级 |
| `equip_category_list` | 6 | 装备类别 id → 名称 |
| `intrinsic_equip_role_map` | 1 | 固有装备 id → 角色 id |
| `system_avatar_map` | 10 | 与本项目无关 |

羁绊名称全集(34):仙舟舰队、奇兽花园、幻月游戏、星间旅人、开拓银轨、千星商会、云上五骁、星核猎手、小小巨人、梦境交织、少女乐队、黄金大饭店(阵营);追击、持续伤害、群攻、连击、击破、召唤、欢愉、反击、能量、治疗、守护(流派);双龙吟、盗火行者、巡海游侠、黑市主理人、机心巧匠、幻乐之主(专属);连携追击、四维狂潮、星狩誓言、救世主、从师(特殊)。

**描述文本含标记,入库时要清洗**:

- `<color=#f29e38ff>文字</color>` → 去掉标签保留文字;
- `<property type=ExtraLuckChance display=all>` → 用 `role_property_list` 把 `type` 映射成中文名(如「幸运一击率」);映射不到的保留原 type 名;
- `<i>…</i>` 及其后的风味文本(装备描述里 `\n\n` 之后的引言)→ 去掉;
- 字面的 `\\n` → 换行。

同时保留一份未清洗的原始 JSON 快照,便于以后重新解析。

#### 3.1.2 阵容库

`POST https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/index`

请求体(已实测可用):

```json
{"game":"hkrpg","page":"1","limit":"10","lineup_type":"Tourn","next_page_token":"",
 "role_ids":[],"trait_ids":[],"match_change_job":false,"match_hard":false,"order":"Hot"}
```

响应 `data.list[]` 为阵容,`data.next_page_token` 用于翻页。`role_ids` / `trait_ids` 可过滤,`match_hard` 过滤高难适配,`order:"Hot"` 为热度排序。

每条阵容的关键字段(已实测):

- `id`、`title`、`description`(作者运营思路,自由文本);
- `game_data.interact.{favour,like,view}`、`game_data.recent_interact.use`(使用数,头部阵容 339 万);
- `tourn_detail`:
  - `season_id`、`sub_season_id`、`is_expired`、`is_sub_expired`、`rpg_game_big_version`:赛季有效性;
  - `share_code`:游戏内攻略码;
  - `support_hard`、`lineup_level`、`has_expert`、`has_change_equip`;
  - `labels[]`:节奏标签(如「速升9级」);
  - `portals[]`:适配的投资环境;
  - `first_fight_augments[]` / `second_fight_augments[]`:推荐的投资策略;
  - `order_basic[]` / `order_compose[]`:简易装备拿取顺序、进阶装备合成顺序;
  - `role_stages[]`:三个阶段 `Early` / `Middle` / `Final`,每个含 `front_roles[]`、`back_roles[]`、`carry_list[]`(主 C 的角色 id)、`switch_role_map`、`traits[]`;
    - 角色项:`id`、`name`、`rarity`、`star`、`is_carry`、`front_back_type`、`first_equipments[]` / 第二套装备、技能摘要;
    - **`traits[]` 带完整档位数据**:`trait_id`、`trait_name`、`trait_type`、`current_role_count`、`layers[]`(每档 `layer` 人数阈值、`quality`、`is_activated`、`trait_desc`)、`remarks[]`(术语解释)。这是羁绊档位阈值和效果的来源:遍历所有阵容的所有阶段,按 `trait_id` 合并去重即可得到每个羁绊的完整档位表(`current_role_count`、`is_activated` 是该阵容的状态,合并时丢弃)。合并后检查 34 个羁绊是否都覆盖到,缺的列出来交给用户用游戏内图鉴补。

实测热度前十全部为 `season 2 / 版本 4.7 / 未过期`,例如「【6连击3奇兽花园】我的天哪,阿格莱雅大人!」「【6欢愉5幻月游戏】养猪厂厂长银狼,前来报到!」「【5千星商会3欢愉】…」「【2双龙吟6开拓银轨】解锁帕姆王…」。

#### 3.1.3 调用要求

- 必要请求头(已实测):`x-rpc-platform: pc`、`x-rpc-currencywar-tourn: tourn`、`x-rpc-device_id: <随机 UUID>`、`referer: https://act.miyoushe.com/`、`origin: https://act.miyoushe.com`;POST 另加 `content-type: application/json`。S1 时实测缺少 tourn / device 头会返回空 data。
- 限速:每个请求间隔 ≥ 1 秒;阵容采集总量控制在数百条(按热度翻页,到 `is_expired` 或达到上限为止)。
- 只做手动触发的同步命令,不做常驻轮询。
- 接口无官方承诺:客户端薄封装,检查 `retcode`,对关键字段做结构快照测试,结构变化时明确报错而不是静默入库。

### 3.2 其他来源

- **B 站 wiki(wiki.biligame.com/sr)**:2026-11-14 查看,S2 的专题页、羁绊页、角色页都只有标题没有内容;装备和投资策略列表未按赛季区分。**不作为数据源**。
- **March7thAssistant**(`tasks/weekly/currency_wars.py`):界面流程和操作方式的参考,事实清单见附录 B。它的固定阵容和费用表是 S1 的,不能用。
- **米游社论坛攻略**:后期可选的补充(蒸馏运营心得),不在前四个里程碑内。

### 3.3 接口没有、需要另外补的信息

| 缺口 | 补法 |
|---|---|
| 奇遇同行者列表与效果 | 用户游戏内截图,人工整理进规则卡或单独的表 |
| 经济数值:利息规则与上限、连胜奖励、经验价格、刷新价格、各等级人口与升级所需经验 | 用户游戏内核实 |
| 节点结构(每位面节点数、类型、阶段号格式)、扣血规则、职级体系 | 用户游戏内核实 |
| S2 商店概率表 | 用户游戏内查看(若游戏内有展示) |
| 个别阵容接口未覆盖到的羁绊档位 | 游戏内图鉴截图 |

---

## 4. 系统架构总览

```
┌──────────── Windows 上的一个后端进程 ────────────┐
│                                                  │
│  截图 → 感知(分类 + OCR/模板) → GameState        │
│                         │                        │
│  知识库 ──实体链接检索──┤                        │
│                         ▼                        │
│        决策:反射层 / 战术层 / 战略层 / 选择类     │
│                         ▼                        │
│        校验器 → 执行器(键鼠) → 动作后校验        │
│                         │                        │
│        Trace 写盘 + 事件流推送                   │
│                                                  │
│  FastAPI:REST + SSE + 托管前端构建产物           │
└──────────────────────────────────────────────────┘
            ▲ 局域网
   浏览器(电脑 / 手机):观战、回放、模型对比、知识库、设置
```

- 对局循环在后端进程内的一个后台线程运行,FastAPI 在主线程提供接口;两者通过线程安全的事件队列通信。
- 所有持久数据都在项目目录下:`data/knowledge.sqlite`、`data/templates/`、`data/fixtures/`、`runs/`、`config.yaml`。
- 每个模块边界都以 pydantic 模型为契约:`GameState`、`Action`、`RunDirection`、`TraceStep`、`ModelProfile`。

---

## 5. 知识库

### 5.1 存储

单文件 sqlite(`data/knowledge.sqlite`)+ YAML 导出(`data/export/*.yaml`,便于人工查看和 git diff)。

```
meta            : season_id、sub_season_id、game_version、同步时间
roles           : 来自 role_list;另加 aliases 列(OCR 纠错用)
role_skills     : 角色 × 站位(Front/Back) × 星级 → 描述
traits          : 来自 trait_info_list + 从 roles 反推的成员列表
trait_layers    : 羁绊 × 档位人数 → 品质、效果描述(来自阵容接口合并)
trait_remarks   : 羁绊术语解释
equipment       : 来自 equipment_list(含原料里出现的简易装备等)
recipes         : 合成配方边表(产物、原料 A、原料 B)
augments        : 投资策略
portals         : 投资环境
spotlights      : 主角光环(含标签、适用角色)
companions      : 奇遇同行者(人工录入)
guides          : 指引卡(见 5.2)+ 原始阵容 JSON
lessons         : 复盘经验(见 5.4)
raw_snapshots   : 接口原始响应
```

### 5.2 指引卡(攻略的呈现形式)

每套官方阵容转成一张指引卡。指引卡是**给模型的参考资料,不是必须执行的脚本**。

```yaml
id: "<阵容 id>"
title: "【6连击3奇兽花园】我的天哪,阿格莱雅大人!"
use_count: 3396971
support_hard: true
tempo_labels: [速升9级]
environments: [连击概念股]            # 适配的投资环境,可为空
core_idea: >                          # 作者 description,过长时用 LLM 摘要成 2–4 句
  前期凑连击和奇兽花园,拿到阿格莱雅之前靠那刻夏输出,后期把那刻夏的装备转给阿格莱雅。
carries: [阿格莱雅, 那刻夏]
final_board:
  front: [阿格莱雅, 风堇, 星期日, 那刻夏]
  back:  [刻律德菈, 藿藿, 寒鸦, 镜流, 知更鸟•晴歌]
key_traits: [{name: 连击, count: 6}, {name: 奇兽花园, count: 3}]
equipment: {阿格莱雅: [反重力皮靴, …]}
equip_order: {basic: [轮滑鞋, 轮滑鞋, …], compose: [反螺旋舞步, …]}
recommended_augments: [市场活力, 采购专员•彩, 节假日礼盒]
example_boards:                        # Early / Middle 板面,仅作示例
  early:  {front: [那刻夏, 长夜月, 星期日], back: [刻律德菈, 白露]}
  middle: {front: [那刻夏, 星期日, 长夜月, 镜流], back: [刻律德菈, 白露, 赛飞儿]}
```

`core_idea` 的摘要是知识同步时唯一可能调用 LLM 的地方,要可跳过(没有配置模型时直接截断原文)。

### 5.3 规则卡

`knowledge/rules.md`,手写,约 1–2k token,常驻系统提示词。内容:对局流程、经济规则、扣血、升星、站位与赋能、装备合成规则、S2 新机制(奇遇、主角光环、阿哈)、通用运营原则。

**所有数值必须来自用户游戏内核实**。未核实的条目在文件里用「【待核实】」标出,并且不注入提示词(加载时过滤掉带该标记的行),以免模型按错误数字决策。

### 5.4 经验库

每局结束后调用一次模型做复盘:输入本局的关键步骤摘要和结果,输出若干条教训(`适用条件` + `教训内容` + `证据:对局 id 和阶段`)。新教训默认 `pending`,用户在前端审核后变为 `approved` 才会注入后续对局。按适用条件(羁绊、阶段、环境)做简单匹配注入,数量上限 10 条。

### 5.5 检索

不使用向量库。三种确定性检索:

1. **实体链接**:GameState 中出现的每个名字(商店角色、场上角色、持有装备、三选一选项、主角光环候选、当前投资环境、已拿的投资策略、当前奇遇同行者)→ 查库,把描述原文附进提示词。场上角色附当前站位和星级对应的技能描述;商店角色附费用、羁绊、站位类型和简述。
2. **羁绊状态**:根据场上角色计算每个相关羁绊的当前人数、已激活档位、下一档还差几人及其效果。
3. **指引卡打分**(战略层用):

```
score = w1·log(1+use_count) + w2·support_hard(高难时)
      + w3·env_match(当前投资环境 ∈ card.environments)
      + w4·overlap(当前已有角色 ∪ 商店角色, card 的 carries 和 final_board)
      + w5·spotlight/companion 相关性(可先置 0)
```

取前 3–5 张。权重放配置里。

提供开关 `use_guides: true|false`,关闭后不给指引卡,用于对比模型不靠攻略时的水平。

---

## 6. 感知层

### 6.1 截图

- 按窗口标题「崩坏:星穹铁道」和窗口类 `UnityWndClass` 定位游戏窗口,取客户区(不含标题栏)截图;进程需声明 DPI 感知,否则坐标会偏。
- 截图用 `mss`。所有区域坐标以 1920x1080 为基准做归一化,集中存放在 `config/regions.yaml`,运行时按实际客户区尺寸换算。
- 感知层的所有函数只接收图像(numpy 数组),不直接碰窗口,这样在非 Windows 平台上也能对 fixtures 完整测试。

### 6.2 画面分类

每帧先判当前界面:`home` / `mode_select` / `difficulty` / `environment_choice` / `companion_reveal` / `prepare` / `battle` / `augment_choice` / `supply` / `spotlight_choice` / `settlement` / `popup_*` / `unknown`。

每个界面 1–3 个锚点:固定 UI 元素的模板匹配 + 关键区域 OCR 关键词。界面定义集中在一个注册表里(界面名、锚点、对应的提取器、是否需要决策),新增界面只加一条。

### 6.3 状态提取

| 信息 | 手段 |
|---|---|
| 金币、生命、等级、经验、阶段号、连胜 | 区域 OCR(RapidOCR) |
| 商店角色名与费用 | 区域 OCR + 角色名全集最近邻纠错(编辑距离) |
| 前台 / 后台 / 备战席的角色 | 官方头像模板匹配为主,名字 OCR 为辅 |
| 星级 | 星标区域模板匹配或颜色计数 |
| 角色身上和背包里的装备 | 官方装备 icon 模板匹配 |
| 三选一、主角光环、投资环境、奇遇的选项文字 | 弹窗区域 OCR + 对应名字全集纠错 |

每个字段带置信度;低于阈值时重新截图再提取一次,仍低则在 GameState 中标记该字段不确定,由决策层保守处理。

### 6.4 模板库

`role_list[].icon` / `big_icon`、`equipment_list[].icon` 等是 CDN 图片地址。知识同步时全部下载,统一缩放,生成 `data/templates/roles/`、`data/templates/equipment/`。游戏内显示的头像可能有裁切、边框、星级角标,匹配前需要按 fixtures 调整裁切区域和匹配方法(这一步只能在拿到 fixtures 后做)。

### 6.5 标定与采集工具

- `cwagent collect`:在 Windows 上运行,用户手动打游戏时按热键或定时截图,保存客户区原图到 `data/fixtures/<日期>/`。
- `cwagent regions check <截图>`:把 `regions.yaml` 中所有区域框和名字画到截图上输出一张图,用于肉眼校对坐标。
- fixtures 按界面归档后,每个提取器都有对应的单测;每张 fixture 配一个人工确认过的期望 GameState(JSON)。

### 6.6 验收

fixtures 上:画面分类 100% 正确;字段提取 ≥ 95% 正确。

---

## 7. GameState

决策层的唯一输入,纯文本 JSON。以下是结构示意,字段可按实际界面增减,但改动要同步到提示词和校验器。

```json
{
  "screen": "prepare",
  "plane": 1, "stage": "1-3",
  "hp": 100, "gold": 23, "level": 5, "exp": "2/10", "win_streak": 2,
  "environment": "连击概念股",
  "companion": "<奇遇同行者名>",
  "augments_taken": ["市场活力"],
  "spotlights": [{"role": "那刻夏", "name": "<主角光环名>"}],
  "shop": [
    {"slot": 0, "name": "那刻夏", "cost": 3, "sold": false},
    {"slot": 1, "name": "白露", "cost": 2, "sold": false}
  ],
  "shop_locked": false,
  "front": [{"idx": 0, "name": "那刻夏", "star": 2, "equips": ["反重力皮靴"]}],
  "back":  [{"idx": 0, "name": "刻律德菈", "star": 1, "equips": []}],
  "bench": [{"idx": 0, "name": "镜流", "star": 1, "equips": []}],
  "front_capacity": 4, "back_capacity": 2, "bench_capacity": 9,
  "equip_inventory": ["轮滑鞋", "生命之花"],
  "pending_choice": null,
  "uncertain_fields": []
}
```

选择类界面时 `screen` 为对应界面,`pending_choice` 为:

```json
{"kind": "augment", "options": [{"idx": 0, "name": "特战资金", "quality": 1}, …], "can_refresh": true}
```

衍生信息(羁绊激活情况、可合成的装备、每个名字的描述)不放在 GameState 里,由检索层在组装提示词时计算。

---

## 8. 决策引擎

### 8.1 分层

| 层 | 触发 | 输入 | 输出 |
|---|---|---|---|
| 反射层(规则) | 每帧 | 界面类型 | 确定性动作(点继续、跳过、领奖、关弹窗);或"需要决策" |
| 选择类(LLM) | 出现三选一类界面 | GameState + 候选项官方描述 + 对局方向 | `choose(idx)` 或刷新 |
| 战略层(LLM) | 开局选完环境后 1 次;触发式重规划 | 开局信息 + 前几张指引卡 | `RunDirection` |
| 战术层(LLM) | 每个备战阶段 1–3 次 | GameState + RunDirection + 实体描述 + 羁绊状态 + 最近动作记录 + 经验 | 有序动作列表 |

战术、战略、选择、复盘各自可在配置里指定模型档案;默认全部用同一个。

### 8.2 对局方向(RunDirection)

模型自己写的方向,不是脚本。字段都是自由文本或名字列表,不设必须达成的检查点。

```yaml
archetype: "连击 + 奇兽花园"
reference_guides: ["<指引卡 id>"]        # 参考了哪些卡,可为空
carry_candidates: [阿格莱雅, 那刻夏]
plan: >
  前期用那刻夏和低费连击角色保连胜;6 级前不主动 D 牌;拿到阿格莱雅后把输出装转给她……
economy_notes: "…"
pivot_conditions: "到位面2中段仍没有任何主C 2星,或出现更契合当前环境的核心角色时考虑转型"
```

重规划触发:战术层在输出里设置 `request_replan: true` 并给出理由;或规则触发(生命低于阈值、连续多个阶段主 C 未成型)。重规划的输入包含旧方向和当前局面。

### 8.3 战术层的动作空间

一次调用通过一个工具 `submit_actions` 提交:

```json
{
  "analysis": "对当前局面的判断(给人看的,会显示在观战页)",
  "actions": [
    {"op": "buy", "args": {"slot": 0}, "reason": "那刻夏凑2星"},
    {"op": "move", "args": {"from": {"zone": "bench", "idx": 0}, "to": {"zone": "back", "idx": 1}}, "reason": "…"},
    {"op": "roll_until", "args": {"targets": ["阿格莱雅"], "gold_floor": 30, "max_rolls": 5}, "reason": "…"},
    {"op": "ready", "args": {}, "reason": "…"}
  ],
  "request_replan": false
}
```

| op | 参数 | 说明 |
|---|---|---|
| `buy` | `slot` | 买商店第几格 |
| `sell` | `zone`, `idx` | 卖掉某格的角色 |
| `refresh` | — | 刷新商店;**其后的动作全部作废**,重新感知后再调用模型 |
| `buy_exp` | `times` | 买经验 |
| `move` | `from`, `to` | 在前台 / 后台 / 备战席之间移动或交换 |
| `equip` | `item`, `zone`, `idx` | 把背包里的装备给某角色 |
| `craft` | `item_a`, `item_b` | 合成(如果游戏里合成是通过把两件装备给同一角色自动完成,则此动作在执行层映射为对应操作;以实际界面为准) |
| `lock_shop` | `locked` | 锁定 / 解锁商店 |
| `roll_until`(宏) | `targets[]`, `gold_floor`, `max_rolls` | 规则循环:刷新 → 感知 → 商店里有目标就买;金币到下限、次数用完或备战席满即停,把结果交回模型 |
| `level_to`(宏) | `level` | 买经验直到指定等级或金币不足 |
| `ready` | — | 结束备战,开始战斗 |

不使用各厂商的 strict 模式(各家不一致);统一用 pydantic 校验模型输出。

### 8.4 校验器

只拦**客观非法**的动作:金币不够、格子已满、目标格为空、名字不存在、配方不成立、站位类型不允许(后台角色放前台等)、参数缺失。非法时把具体错误信息连同原输出一起重问,最多 2 次;仍失败则执行已通过校验的前缀动作后直接 `ready`。

不设"必须遵循攻略"的约束。只有一条防误操作规则:卖出 2 星及以上角色时 `reason` 不能为空。

每个备战阶段战术调用上限 3 次(`refresh` 和宏返回会消耗次数);超限直接 `ready`。上限放配置里。

### 8.5 提示词

每个决策点是一次**无状态调用**(system + 一条 user),不维护多轮对话。跨厂商最稳,也让离线回放和模型对比成为可能。

- **system**(稳定,利于缓存):角色设定、规则卡、动作语义与约束、输出要求(先写 `analysis` 再给动作;每个动作必须有 `reason`;用中文)。
- **user**(每次变化),按固定顺序拼:
  1. 对局方向(RunDirection);
  2. GameState;
  3. 羁绊状态(当前人数 / 已激活档 / 下一档差几人及效果);
  4. 屏幕实体描述(实体链接结果);
  5. 可合成的装备(根据背包和配方算出);
  6. 本备战阶段已执行的动作及结果、上一场战斗的结果;
  7. 适用的经验条目;
  8. 本次需要做什么。

提示词模板放在 `src/cwagent/brain/prompts/` 下的独立文件里,不要散落在代码中;Trace 里保存每次实际发送的完整提示词。

写提示词时:说明这是一个什么游戏、目标是什么、攻略只是参考;不要堆砌"必须""绝对"这类措辞,不要塞示例动作序列(模型会照抄)。

### 8.6 成本粗估

每局约 50 次战术调用 + 若干选择类调用,每次输入约 5–10k token。旗舰模型每局可能在几美元量级,便宜模型几美分。这是估计,以第三阶段实测为准;Trace 里记录每次调用的 token 用量,前端汇总显示。

---

## 9. 执行层

- 键鼠原语:`click(nx, ny)`、`drag(from, to)`、`press(key)`,坐标为归一化值。输入库先用 `pyautogui`;如果游戏窗口不响应合成事件,依次降级到 `pydirectinput`、Win32 `SendInput`。游戏以管理员权限运行时,agent 进程也必须以管理员权限运行。
- 每个语义动作 = 原语序列 + 前置断言(对应按钮或格子存在)+ 后置校验(金币变化、格子内容变化、商店槽变空)。失败重试 1 次,仍失败则放弃本次动作列表的剩余部分,回到重新感知。
- 已知可用的快捷键(S1 时期,S2 待核实):`D` 刷新商店、`F` 买经验、`Esc` 退出 / 取消。站位移动在 M7A 中用拖拽实现(按下 → 移动 → 抬起)。
- 动作之间加入小的随机延迟。
- 安全措施:
  - 只在分类器识别为货币战争相关界面时才执行动作;识别到其他界面(抽卡、商城、充值等)立即停止并报警;
  - `--approve` 开关:每个动作列表执行前在终端或观战页等待人工确认(首次联调用);
  - 急停:鼠标移到屏幕角落(pyautogui failsafe)或观战页点停止;
  - 单局步数上限和时长上限(默认 120 分钟)。

---

## 10. 编排与可靠性

主循环状态机:

```
Detect(截图 + 分类)
  ├ 已知界面、无需决策 → Reflex → Execute → Detect
  ├ 已知界面、需要决策 → Decide → Validate → Execute → Detect
  ├ 战斗中 → 等待(定时重新 Detect)
  └ 未知 / 卡死 → Recover → Detect
```

- **卡死判定**:阶段号连续多次未变化且界面无进展,或连续多次未知界面。
- **恢复流程**:`Esc` 逐级退出 → 重新进入 → 必要时「放弃并结算」。恢复时保存截图到 `runs/<id>/debug/`。
- **对局控制**:开始、暂停(在下一个决策点前停住)、继续、停止;可从命令行或观战页触发。
- **未结算的旧对局**:进入时若发现有未结算对局(M7A 中的识别文字是「结束并结算」/「放弃并结算」),按配置选择继续或放弃。

---

## 11. LLM 接入层

### 11.1 原则

- 不安装 `anthropic`、`openai` 等厂商 SDK,用 `httpx` 直接发请求;
- 一个模型档案只有 URL、key、模型名和格式,其余都是可选的透传参数;
- 代码里不出现任何具体模型名或按模型名分支的逻辑,换模型只改档案。

### 11.2 模型档案

```yaml
models:
  - name: opus                      # 档案名,界面上显示和选择用
    format: anthropic               # anthropic | openai
    base_url: https://api.anthropic.com
    api_key: sk-ant-…
    model: claude-opus-5-5
    max_tokens: 16000               # anthropic 格式必填;openai 格式不填则不发送
    extra_body:                     # 原样合并进请求体
      thinking: {type: adaptive, display: summarized}
      output_config: {effort: medium}
    extra_headers: {}               # 原样合并进请求头
    tool_mode: native               # native | json
    timeout_seconds: 300

  - name: some-openai-compatible
    format: openai
    base_url: https://api.example.com/v1
    api_key: sk-…
    model: some-model-id
    extra_body: {reasoning_effort: high}
```

`config.yaml` 含 key,必须在 `.gitignore` 中;仓库里放 `config.example.yaml`。后端接口返回档案时 key 只显示掩码(前 4 位 + 后 4 位)。

### 11.3 统一接口

```python
class LLMResponse(BaseModel):
    text: str                 # 可见文本
    reasoning: str            # 思考内容(没有则为空)
    tool_calls: list[ToolCall]   # name + arguments(dict)
    usage: Usage              # input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
    stop_reason: str
    latency_s: float
    raw: dict                 # 原始响应,进 Trace

def complete(profile, system: str, user: str, tools: list[ToolSpec]) -> LLMResponse
```

`ToolSpec` = `name` + `description` + `parameters`(JSON Schema)。两个适配器各自只做请求体构造和响应解析,放在 `llm/openai_format.py` 和 `llm/anthropic_format.py`。

### 11.4 OpenAI 格式

- `POST {base_url}/chat/completions`(`base_url` 自带 `/v1`),头:`Authorization: Bearer <key>`、`Content-Type: application/json`。
- 请求体:

```json
{"model": "…",
 "messages": [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}],
 "tools": [{"type": "function", "function": {"name": "…", "description": "…", "parameters": {…}}}],
 "tool_choice": "auto"}
```

- 响应解析:`choices[0].message.content` → text;`choices[0].message.tool_calls[].function.{name, arguments}`(`arguments` 是 JSON 字符串,需解析)→ tool_calls;思考依次尝试 `message.reasoning_content`、`message.reasoning`;`usage.prompt_tokens` / `completion_tokens` → usage;`choices[0].finish_reason` → stop_reason。

### 11.5 Anthropic 格式

- `POST {base_url}/v1/messages`,头:`x-api-key: <key>`、`anthropic-version: 2026-10-21`、`Content-Type: application/json`。版本号做成档案可覆盖的字段(第三方网关可能只认旧版本号 `2023-06-01`)。
- 请求体:

```json
{"model": "…", "max_tokens": 16000,
 "system": [{"type": "text", "text": "…", "cache_control": {"type": "ephemeral"}}],
 "messages": [{"role": "user", "content": "…"}],
 "tools": [{"name": "…", "description": "…", "input_schema": {…}}]}
```

- 响应解析:遍历 `content[]`,`type=="text"` → text,`type=="thinking"` → reasoning(取 `thinking` 字段),`type=="tool_use"` → tool_calls(`name`、`input` 已是对象);`usage.input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` → usage;`stop_reason` 原样保留。
- 注意事项(截至 2026-11 的 Claude API 行为,写进适配器注释和 `config.example.yaml`):
  - 不要发送 `temperature` / `top_p` / `top_k`,新版 API 会拒绝;
  - 新一代 Claude 模型不接受强制工具调用(`tool_choice` 为 `any` 或指定工具会 400),所以始终用默认的 auto,靠提示词要求调用工具;没有返回工具调用时按 11.6 重问;
  - 思考内容默认不返回,需要在档案的 `extra_body` 里写 `thinking: {type: adaptive, display: summarized}` 才能拿到摘要;较老的模型用 `thinking: {type: enabled, budget_tokens: N}`。这些都由档案决定,代码不判断;
  - `stop_reason == "refusal"` 时当作一次失败调用处理并记入 Trace;不启用服务端自动回退到其他模型的功能,因为那会让别的模型代答,破坏模型对比。

### 11.6 工具调用的统一与兜底

- `tool_mode: native`:走各格式的原生工具调用。响应里没有工具调用时,带一句提示重问 1 次。
- `tool_mode: json`:不传 `tools`,在 user 末尾附上工具的 JSON Schema 和"只输出一个 JSON 对象"的要求,从回复文本中提取 JSON(取最后一个完整的 JSON 对象或 ```json 代码块)。给不支持工具调用的模型和网关用。
- 两种模式的结果都经过同一个 pydantic 校验。

### 11.7 重试与错误

- 429、5xx、网络错误:指数退避重试(默认最多 4 次),有 `retry-after` 头时遵循;
- 其他 4xx:不重试,抛出带状态码和响应体的错误,记入 Trace,前端显示;
- 连续 3 个决策点调用失败则暂停对局并报警。

### 11.8 连通性测试

设置页的"测试"按钮调用后端接口,用该档案发一个最小请求(一句话 + 一个简单工具),返回是否成功、是否拿到工具调用、是否拿到思考内容、耗时和 token 用量。

---

## 12. Trace、回放与评测

### 12.1 Trace

`runs/<时间戳>_<档案名>/`:

- `meta.json`:模型档案(不含 key)、配置快照、赛季版本、难度、开始 / 结束时间、结果;
- `steps.jsonl`:每个循环步一条:

```json
{"step": 37, "ts": "…", "screen": "prepare", "shot": "shots/0037.jpg",
 "state": {…GameState…},
 "decision": {"layer": "tactical", "profile": "opus",
              "system": "…", "user": "…",
              "response": {"text": "…", "reasoning": "…", "tool_calls": […], "usage": {…}, "latency_s": 4.2},
              "validation": {"ok": true, "errors": [], "retries": 0}},
 "actions": [{"op": "buy", "args": {"slot": 0}, "reason": "…", "result": "ok"}]}
```

- `shots/`:每步截图(JPEG);`debug/`:异常时的原图。

反射层的步骤也记录(`decision` 为空),这样回放是完整的。

### 12.2 离线回放与模型对比

`cwagent replay <run> --models A,B [--steps 10-40]`:取历史对局中的决策步,用同样的 `system` / `user` 调用其他模型,结果存到 `runs/<run>/replays/<档案名>.jsonl`。只比较"同一局面下各模型会怎么做",不执行动作。前端模型对比页读这些文件并排展示。

### 12.3 实机评测

固定模式和难度,每个模型跑 N 局。指标:通关率、到达的最远阶段、剩余生命、非法动作率、重问次数、每局调用次数 / token / 耗时 / 估算费用。商店和事件随机性大,少量对局只能看趋势,报表里要显示样本量。

---

## 13. 后端接口

FastAPI。默认监听 `127.0.0.1`;配置 `server.host: 0.0.0.0` 后局域网可访问(README 提醒这会把控制权和知识库暴露给局域网)。没有登录机制。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/status` | 当前是否有对局、状态、所用档案 |
| `POST /api/run/start` `…/pause` `…/resume` `…/stop` | 对局控制 |
| `POST /api/run/approve` | `--approve` 模式下确认或拒绝当前动作列表 |
| `GET /api/events` | SSE 事件流 |
| `GET /api/runs` `GET /api/runs/{id}` `GET /api/runs/{id}/steps` | 对局列表、元信息、步骤(分页) |
| `GET /api/runs/{id}/shots/{name}` | 截图 |
| `POST /api/runs/{id}/replay` `GET /api/runs/{id}/replays` | 发起和读取离线回放 |
| `GET /api/eval/summary` | 实机评测汇总 |
| `GET /api/knowledge/{kind}` `GET /api/knowledge/{kind}/{id}` | 知识库浏览(roles / traits / equipment / augments / portals / spotlights / companions / guides) |
| `POST /api/knowledge/sync` | 触发知识同步 |
| `GET /api/lessons` `PATCH /api/lessons/{id}` | 经验条目查看与审核 |
| `GET /api/models` `POST` `PUT /api/models/{name}` `DELETE` | 模型档案管理(key 掩码) |
| `POST /api/models/{name}/test` | 连通性测试 |
| `GET /api/config` `PUT /api/config` | 运行参数 |

SSE 事件类型:`run_status`(开始 / 暂停 / 结束 / 报错)、`step`(一条完整的 TraceStep,截图以 URL 给出)、`awaiting_approval`、`log`。

---

## 14. 前端

### 14.1 技术

React + Vite + TypeScript,放在仓库 `web/` 目录;`npm run build` 产出 `web/dist/`,由后端作为静态文件托管,所以运行时只有一个服务。开发时用 Vite 开发服务器并把 `/api` 代理到后端。

组件库:**ARC UI**(GitHub `arclight-digital/arc-ui`,基于 Lit 的 Web Components,暗色优先)。安装:

```bash
npm install @arclux/arc-ui-react @arclux/arc-ui lit
```

```tsx
import { Button, Card } from '@arclux/arc-ui-react';
```

React 封装里,自定义事件以 `onArc…` 形式暴露,数据在 `event.detail` 上(如 `onArcInput={(e) => e.detail.value}`)。组件的具体属性和用法**不要凭记忆写**,用 Context7 查文档:库 id 为 `/arclight-digital/arc-ui`(`npx ctx7@latest docs /arclight-digital/arc-ui "<要查的内容>"`)。该库有 App Shell、Split Pane、Tabs、Sidebar、Card、Stat、Timeline、Chart、Data Grid、Table、Drawer、Dialog、Toast、Markdown、Code Block 等组件,优先用库里已有的,不要自己重写同类组件。

### 14.2 页面

| 页面 | 内容 |
|---|---|
| **观战** | 左:当前截图;中:由 GameState 画出的板面(前台 / 后台 / 备战席 / 商店 / 背包,角色用官方头像)、金币 / 生命 / 等级 / 阶段;右:模型的 `analysis`、思考内容(可折叠)、动作队列及每个动作的理由和执行结果。底部:金币和生命随阶段变化的曲线。顶部:对局控制(开始时选档案和难度、暂停、停止)和 `--approve` 的确认按钮。通过 SSE 实时更新 |
| **对局记录** | 历史对局表(时间、档案、难度、结果、到达阶段、步数、token);点进去是逐步回放:时间线选步,显示当时的截图、状态、完整提示词、思考、输出、校验结果 |
| **模型对比** | 选一局和一段决策步、选若干档案,发起离线回放;按步并排显示各模型的 `analysis`、思考和动作,标出动作不同的步。另一个标签页显示实机评测汇总表和图 |
| **知识库** | 分标签浏览角色、羁绊(含档位)、装备(含配方)、投资策略、投资环境、主角光环、奇遇、指引卡;可搜索;经验条目的审核(通过 / 驳回 / 编辑);显示当前数据的赛季版本和同步时间,带同步按钮 |
| **设置** | 模型档案的增删改和连通性测试;各决策层使用哪个档案;运行参数(难度、调用上限、步数上限、是否使用指引卡等) |

界面文字用中文。

---

## 15. 工程结构、依赖与命令

```
hsr-currency-wars-agent/
├── LICENSE  NOTICE  README.md
├── pyproject.toml                # uv;python >= 3.11
├── config.example.yaml           # config.yaml 不入 git
├── config/regions.yaml           # 1920x1080 归一化区域
├── knowledge/rules.md            # 规则卡
├── docs/TECH_DESIGN.md           # 本文档
├── src/cwagent/
│   ├── capture/                  # 窗口定位、截图(Windows 实现 + 其他平台桩)
│   ├── vision/                   # ocr / template / classifier / extractors / regions
│   ├── state/                    # GameState、Action、RunDirection 等模型
│   ├── knowledge/                # official_api / db / clean(标记清洗) / guides / retrieve / lessons
│   ├── llm/                      # base / openai_format / anthropic_format / json_mode
│   ├── brain/                    # reflex / strategist / tactician / chooser / validator / prompts/
│   ├── actuator/                 # input / actions / verify
│   ├── orchestrator/             # loop / recovery / run_control
│   ├── replay/                   # trace / replayer / evaluator / collector
│   ├── server/                   # FastAPI 应用、路由、事件流
│   └── cli.py
├── web/                          # 前端
├── data/                         # knowledge.sqlite / templates / fixtures / export(大文件不入 git)
├── runs/                         # 对局记录(不入 git)
└── tests/
```

后端依赖:`httpx`、`pydantic`、`pyyaml`、`fastapi`、`uvicorn`、`typer`、`rapidocr-onnxruntime`、`opencv-python`、`numpy`、`pillow`、`mss`、`pyautogui`;开发依赖 `pytest`。不安装 `anthropic`、`openai`。仅 Windows 需要的依赖用环境标记限定。

命令:

| 命令 | 作用 | 平台 |
|---|---|---|
| `cwagent knowledge sync` | 拉官方 config 和阵容库入库,合并羁绊档位,下载模板 | 任意 |
| `cwagent collect` | 手动游玩时截图采集 fixtures | Windows |
| `cwagent regions check <截图>` | 画出区域框供校对 | 任意 |
| `cwagent play --model X [--approve]` | 全自动对局 | Windows |
| `cwagent replay <run> --models A,B` | 离线回放对比 | 任意 |
| `cwagent review <run>` | 复盘生成经验条目 | 任意 |
| `cwagent serve` | 启动后端和前端页面 | 任意 |

**现状**:仓库目前只有本文档和 uv 项目骨架(`pyproject.toml` 的依赖随功能逐步加入),业务代码尚未开始。

---

## 16. 开发工作流

1. **任意平台可完成并自测**:知识同步(接口可直连)、标记清洗、模板库、LLM 接入层、决策层(对手写的 GameState 和 fixtures)、离线回放、后端接口、前端全部页面、感知层算法(对 fixtures)。
2. **必须在 Windows 上**:截图与键鼠、`collect` 采集、执行层联调、实机对局。在其他电脑上开发时,推送后在 Windows 上 `git pull`,再通过 SSH 运行。
3. **SSH 会话看不到用户桌面**:在 SSH 里直接启动的进程截图是黑的、键鼠无效。需要接触游戏的进程(agent 服务、采集工具)要启动到用户已登录的桌面会话里,例如用计划任务(`schtasks /Create ... /IT /RL HIGHEST` 后 `schtasks /Run`)。之后通过 agent 后端接口、日志文件和 `runs/` 目录观察与调试。
4. 每个依赖 Windows 的功能都要有不依赖 Windows 的测试路径:截图源可替换为"从目录读图片",输入设备可替换为"只记录不执行"的假实现。用这两个替身可以在任何平台上把整条主循环对着一组 fixtures 跑通。
5. 报告进度时如实区分"已在非 Windows 平台验证"和"已在 Windows 实机验证"。

---

## 17. 里程碑与验收

| 阶段 | 内容 | 验收 | 依赖 |
|---|---|---|---|
| **M0 底座** | 脚手架;LLM 接入层(两种格式);知识同步、清洗、羁绊档位合并、模板库;`collect` 和 `regions check`;后端骨架;前端骨架 + 设置页 + 知识库页 | `cwagent knowledge sync` 后库中有 63 角色 / 105 装备 / 298 投资策略 / 73 投资环境 / 82 主角光环 / 34 羁绊及其档位 / 数百张指引卡(数字以当时接口为准,与接口返回一致即可);假 HTTP 响应的单测覆盖两种格式的请求构造与解析;设置页能添加两种格式的档案并通过连通性测试;知识库页能浏览全部数据 | 任意平台 |
| **M1 感知** | 画面分类、全字段提取、S2 区域标定 | fixtures 上分类 100%、字段 ≥ 95% | Windows 上采集的 fixtures |
| **M2 决策** | 战略层、战术层、选择类、校验器、Trace、离线回放;前端对局记录页 + 模型对比页 | 在手写状态和 fixtures 状态上,2 个以上模型能在对比页并排给出合法决策 | 可与 M1 并行 |
| **M3 全自动** | 执行层、反射层、主循环、卡死恢复、对局控制;前端观战页 | 最低难度无人工干预通关 1 局,全程可在观战页实时看到 | Windows 实机 |
| **M4 提升** | 复盘经验库、实机评测、逐级提高难度 | 多模型各 N 局的对比报表;每次失败有归因 | 全部 |

M0 完成后要明确告诉用户:现在需要他在 Windows 上采集 fixtures 并核实第 18 节的事项,M1 和 M3 才能继续。

---

## 18. 需要用户提供或核实的事项

**环境**

- [ ] Windows:游戏 1920x1080 窗口模式、关 HDR、主显示器;装好 Python、uv、Node;准备小号
- [ ] 要对比的模型的 URL 和 API key

**截图**(M0 完成后用 `collect` 采集,手动打 2–3 局)

- [ ] 每类界面至少若干张:主页、模式选择、难度选择、投资环境选择、奇遇揭示、备战(不同等级、不同人口)、战斗中、投资策略三选一、补给、主角光环选择(选角色和选效果)、结算、各种弹窗
- [ ] 备战界面中:商店有空槽、角色带装备、不同星级、装备背包打开、角色详情
- [ ] 图鉴中羁绊档位页面(用于补接口未覆盖的羁绊)和奇遇同行者列表

**规则核实**(写入 `knowledge/rules.md`)

- [ ] 备战阶段有没有倒计时
- [ ] `D` 刷新、`F` 买经验、`Esc` 在 S2 是否仍有效;站位是拖拽还是点选;装备怎么穿戴和合成
- [ ] 利息规则和上限、连胜奖励、刷新价格、买经验的价格和每次经验值、各等级升级所需经验和人口
- [ ] 前台 / 后台 / 备战席的格数及其随等级的变化;每角色装备上限
- [ ] 每个位面的节点数和类型、阶段号格式;战斗失败 / 超时的扣血规则;初始生命
- [ ] 主角光环出现的时机和次数;奇遇的完整列表和效果;阿哈的升费规则
- [ ] 标准博弈与超频博弈的差别;职级名称和难度选择界面的结构
- [ ] 游戏内是否展示商店概率表

---

## 19. 风险

| 风险 | 对策 |
|---|---|
| 封号 | 小号;拟人化延迟;README 写明 |
| 开发机不是 Windows 时无法直接实机验证 | 截图源和输入设备可替换;fixtures 和回放优先;Windows 上 `git pull` 后通过 SSH 运行,读日志和 `runs/` 排查 |
| S2 界面坐标未知 | 所有坐标集中在 `regions.yaml`;`regions check` 校对;M1 等 fixtures |
| 官方接口变更或加鉴权 | 薄封装、结构快照测试、保留原始快照;最后一次成功同步的数据可继续用 |
| 版本更新改 UI 或机制 | 入库记录赛季版本;模板库可重建;过期阵容按 `is_expired` 过滤 |
| OCR / 模板误识别 | 名字全集纠错、置信度、动作后校验、低置信度重截图 |
| 模型输出不合法或不稳定 | pydantic 校验、带错误重问、保守收尾、调用上限 |
| 模型自由度高导致乱打 | 规则卡、实体描述、指引卡提供足够信息;经验库逐局修正;校验器拦客观错误 |
| 未核实的规则数值误导模型 | 规则卡中「【待核实】」条目不注入提示词 |
| 局域网暴露控制接口 | 默认只监听本机;key 永不明文返回 |

---

## 附录 A:接口调用样例(2026-11-14 实测)

```bash
curl -s 'https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/config?game=hkrpg' \
  -H 'x-rpc-platform: pc' -H 'x-rpc-currencywar-tourn: tourn' \
  -H "x-rpc-device_id: <随机UUID>" \
  -H 'referer: https://act.miyoushe.com/' -H 'origin: https://act.miyoushe.com'
```

角色样例(节选):

```json
{"id": "110702", "name": "克拉拉", "rarity": "5", "front_back_type": "Common",
 "trait_details": [
   {"id": "100402", "name": "星间旅人", "desc": "【星间旅人】角色会为你的小队提高特定属性,随场上【星间旅人】角色数量而提升。\\n【星间旅人】队员获得双倍收益。"},
   {"id": "200802", "name": "反击", "desc": "装备最多的前台【反击】角色成为【反击王】:更易受到攻击并【反伤】。\\n进入战斗时,每个后台【反击】角色为其提供30%<property type=ExtraAllDamageTypeAddedRatio1 display=all>,10%<property type=ExtraHPAddedRatio1 display=all>。…"},
   {"id": "300502", "name": "机心巧匠", "desc": "每进入2个新节点,克拉拉就可对史瓦罗进行<color=#f29e38ff>强化改装</color>,…"}],
 "skills": [
   {"front_back_type": "Front", "name": "改装大师", "category_tags": ["输出"],
    "skill_stars": [{"star": 1, "desc": "…"}, {"star": 2, "desc": "…"}, {"star": 3, "desc": "…"}]},
   {"front_back_type": "Back", "name": "改装大师", "category_tags": ["输出", "辅助"], "skill_stars": […]}]}
```

羁绊档位样例(来自阵容接口 `role_stages[].traits[]`):

```json
{"trait_id": "200402", "trait_name": "连击", "trait_type": 1, "current_role_count": 6,
 "layers": [
   {"layer": 2, "quality": 0, "is_activated": true,  "trait_desc": "15%<property type=ExtraLuckChance display=all>。<color=#f29e38ff>10%</color><property type=ExtraComboRate display=icon>【连击率】。"},
   {"layer": 4, "quality": 1, "is_activated": true,  "trait_desc": "30%…,后台1号位额外生成【连击地格】。…"},
   {"layer": 6, "quality": 2, "is_activated": true,  "trait_desc": "45%…,连击时额外造成60%真实伤害。"},
   {"layer": 8, "quality": 2, "is_activated": false, "trait_desc": "60%…,连击时额外造成90%真实伤害。"}],
 "remarks": [{"remark": "【连击率】:施放普攻或战技后,每1%【连击率】有1%概率获得额外回合,…"}]}
```

投资策略样例:`{"id": "100101", "name": "特战资金", "desc": "进入首领节点时,获得7枚金币。", "quality": 1}`

装备配方样例:`财富宝钻` 的 `compose_list[0].childrens` 为两个 `红钻`;`反重力皮靴` = `轮滑鞋` + `轮滑鞋`。

## 附录 B:March7thAssistant 参考事实(S1 时期,S2 需重新确认)

来源:`https://github.com/moesnow/March7thAssistant/blob/main/tasks/weekly/currency_wars.py`(GPL-3.0)。

- 入口链路:主页 → 模式选择(标准 / 超频)→ 难度选择 → 开始对局;结束后领奖。
- 对局内界面标记:左上角退出按钮的模板图。
- 开局确认文字:「下一步」「投资环境」「请选择投资策略」「遭遇节点」。
- 阶段号:OCR 匹配 `^\d-\d$`(如 `1-1`);区域约 `(414/1920, 56/1080, 117/1920, 44/1080)`(x, y, w, h)。
- 金币数区域约 `(1559/1920, 880/1080, 127/1920, 82/1080)`。
- 前台第 1 格约 `(684/1920, 324/1080, 123/1920, 145/1080)`;备战席第 1 格约 `(382/1920, 846/1080, 115/1920, 134/1080)`。
- 快捷键:`D` 刷新商店、`F` 买经验、`Esc` 退出 / 取消。
- 站位移动:鼠标按下 → 移动 → 抬起的拖拽。
- 战斗中检测:暂停按钮模板。
- 常见按钮文字:开始对局、放弃并结算、结束并结算、出售、装备推荐、合成、穿戴、出战、跳过、本局不再提示、确认选择、返回最高职级。
- 卡死判定:阶段号连续 5 次不变或连续 5 次识别失败;总超时 120 分钟;连续 5 分钟无可识别界面则中止并保存截图。

## 附录 C:S1 时期商店概率表(来自 B 站 wiki,S2 是否沿用待核实,不要写进规则卡)

| 等级 | 1费 | 2费 | 3费 | 4费 | 5费 |
|---|---|---|---|---|---|
| 1–3 | 100% | – | – | – | – |
| 4 | 65% | 25% | 10% | – | – |
| 5 | 45% | 33% | 20% | 2% | – |
| 6 | 30% | 40% | 25% | 5% | – |
| 7 | 19% | 30% | 40% | 10% | 1% |
| 8 | 18% | 25% | 32% | 22% | 3% |
| 9 | 15% | 20% | 25% | 30% | 10% |
| 10 | 5% | 10% | 20% | 40% | 25% |

## 附录 D:参考资料

- 官方专题页:`https://act.miyoushe.com/sr/event/currency-wars/index.html#/lineup/home?tab=recommend`
- March7thAssistant:`https://github.com/moesnow/March7thAssistant`
- ARC UI:`https://github.com/arclight-digital/arc-ui`(Context7 库 id `/arclight-digital/arc-ui`)
- S2 改动的第三方综述(内容待核实):`https://www.u7buy.com/blog/hsr-currency-wars-2-0-guide/`
- B 站 wiki 货币战争总览(S1 规则较全,S2 页面为空):`https://wiki.biligame.com/sr/货币战争`
