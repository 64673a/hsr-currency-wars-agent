---
name: cwagent-play
description: 通过 cwagent MCP 游玩或接手崩坏星穹铁道货币战争。只在用户要求开始或接手对局时使用;截图和连通性测试不开始对局。
---

调用 cwagent 的 rules 读取操作和规则,以当前截图和执行结果决策。开始新局用 start_run,默认标准博弈 A8-1;接手已有对局先用 status、snapshot,再用 wait。

每次 act 填入最近局面的 turn,局势判断放 analysis,没采用的做法放 alternatives,操作放 actions(每个带 reason)。图片标号只对应当前观察。工具超时后先 wait 获取待回答的局面,不要直接重复上一组操作。

返回等待时继续 wait;返回结算、暂停、停止或错误时汇报。用户明确要求停止时调用 stop_run。未经新的开局指令,结束后不再开下一局。

上下文压缩后重新调用 rules 和 snapshot。服务按增量交付事实和经历,snapshot 返回完整档案及最近截图。模型的计划写 run_notes,识别冲突写 perception_issues。

只做测试时使用 status、observe、snapshot。observe 不发送键鼠、不创建对局。
