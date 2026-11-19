---
name: play
description: 用 cwagent 下一局《崩坏:星穹铁道》货币战争,一直下到对局结束。用户要玩、开始或接手货币战争对局时使用。
argument-hint: "[难度,如 A8-1]"
allowed-tools: mcp__plugin_cwagent_cwagent__rules mcp__plugin_cwagent_cwagent__start_run mcp__plugin_cwagent_cwagent__wait mcp__plugin_cwagent_cwagent__act mcp__plugin_cwagent_cwagent__lookup mcp__plugin_cwagent_cwagent__status mcp__plugin_cwagent_cwagent__stop_run mcp__plugin_cwagent_cwagent__snapshot mcp__plugin_cwagent_cwagent__observe
hooks:
  PostToolUse:
    - matcher: "mcp__plugin_cwagent_cwagent__.*"
      hooks:
        - type: mcp_tool
          server: "plugin:cwagent:cwagent"
          tool: hook
          input: { event: tool, session: "${session_id}" }
  Stop:
    - hooks:
        - type: mcp_tool
          server: "plugin:cwagent:cwagent"
          tool: hook
          input: { event: stop, session: "${session_id}" }
---

用 cwagent 的工具下一局货币战争,一直下到对局结束。难度:「$ARGUMENTS」(为空时不传 difficulty,用设置里的)。

1. 调用 rules 读玩法和操作说明;上下文被压缩后再读一次,并调用 snapshot 恢复完整档案。
2. 调用 start_run 开始对局。对局已经在网页上开始时改调用 wait。
3. 拿到局面就自己判断,调用 act 提交。局势判断、不选的其他做法和每个操作的理由写在 act 的 analysis、alternatives、reason 里,不用在对话里复述局面或解释操作。
4. 返回「还在等待」时直接再调用 wait,不要停下来问用户。
5. 返回「对局结束」后停下,向用户简短汇报这一局;用户要求时才再开一局。用户要你停下时调用 stop_run。
