"""流式调用和「模型决策中」的进度文件(维护者 2026-11-18 要求:模型还没答完就在网页上看到这一步、思考和判断)。"""

import json
import time

import httpx

from cwagent.config import ModelEndpoint
from cwagent.llm import client as llm_client
from cwagent.llm import complete
from cwagent.orchestrator.pending import PendingDecision, partial_string
from tests.test_llm import TOOL, mock, profile


def sse(*events, done=False):
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events]
    return ("".join(lines) + ("data: [DONE]\n\n" if done else "")).encode()


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def chunk(delta=None, finish=None, usage=None):
    out = {"model": "m-2026", "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        out["usage"] = usage
    return out


def test_openai_stream_rebuilds_the_same_response_and_reports_deltas():
    args = json.dumps({"actions": [{"op": "buy"}]})
    body = sse(chunk({"role": "assistant", "reasoning_content": "金币 23,"}),
               chunk({"reasoning_content": "商店有那刻夏"}),
               chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                      "function": {"name": "submit_actions", "arguments": args[:10]}}]}),
               chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[10:]}}]}),
               chunk({}, finish="tool_calls"),
               chunk(usage={"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 64}), done=True)
    http, seen = mock(lambda req, n: stream_response(body))
    deltas = []
    resp = complete(profile("openai"), "系统", "局面", [TOOL], http=http, on_delta=lambda k, t: deltas.append((k, t)))
    sent = json.loads(seen[0].content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert deltas[0] == ("reset", "") and ("reasoning", "金币 23,") in deltas
    assert "".join(t for k, t in deltas if k == "tool") == args
    assert resp.reasoning == "金币 23,商店有那刻夏" and resp.tool_calls[0].arguments == {"actions": [{"op": "buy"}]}
    assert resp.stop_reason == "tool_calls" and resp.usage.input_tokens == 100 and resp.usage.cache_read_tokens == 64
    # 下一轮原样传回:DeepSeek 思考模式带工具时必须带 reasoning_content
    assert resp.message_raw["reasoning_content"] == "金币 23,商店有那刻夏"


def test_anthropic_stream_keeps_thinking_signature_and_tool_input():
    events = [
        {"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "content": [],
                                              "model": "claude", "usage": {"input_tokens": 50, "cache_read_input_tokens": 30}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先看商店"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "t1", "name": "submit_actions", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"actions": '}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "[]}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ]
    http, seen = mock(lambda req, n: stream_response(sse(*events)))
    deltas = []
    resp = complete(profile("anthropic"), "系统", "局面", [TOOL], http=http, on_delta=lambda k, t: deltas.append((k, t)))
    assert json.loads(seen[0].content)["stream"] is True
    assert ("reasoning", "先看商店") in deltas and "".join(t for k, t in deltas if k == "tool") == '{"actions": []}'
    assert resp.reasoning == "先看商店" and resp.tool_calls[0].arguments == {"actions": []}
    assert resp.message_raw[0] == {"type": "thinking", "thinking": "先看商店", "signature": "sig"}   # 原样传回
    assert resp.stop_reason == "tool_use" and resp.usage.output_tokens == 12 and resp.usage.cache_read_tokens == 30


def test_stream_error_event_is_retried():
    ok = [{"type": "message_start", "message": {"model": "claude", "content": [], "usage": {}}},
          {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
          {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好"}},
          {"type": "content_block_stop", "index": 0},
          {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}]
    overloaded = [{"type": "message_start", "message": {"model": "claude", "content": []}},
                  {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
    http, seen = mock(lambda req, n: stream_response(sse(*(overloaded if n == 1 else ok))))
    resp = complete(profile("anthropic"), "系统", "局面", http=http, sleep=lambda s: None, on_delta=lambda k, t: None)
    assert resp.text == "好" and len(seen) == 2 and resp.attempts == 2


def test_gateway_without_stream_support_falls_back_and_is_remembered():
    llm_client._NO_STREAM.clear()
    reply = {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "好"}}], "usage": {}}

    def handler(req, n):
        if json.loads(req.content).get("stream"):
            return httpx.Response(400, json={"error": {"message": "stream_options is not supported"}})
        return httpx.Response(200, json=reply)

    http, seen = mock(handler)
    endpoint = ModelEndpoint(name="p", format="openai", base_url="https://gw.example.com/v1", api_key="k", model="m")
    assert complete(endpoint, "系统", "局面", http=http, on_delta=lambda k, t: None).text == "好"
    assert len(seen) == 2
    complete(endpoint, "系统", "局面", http=http, on_delta=lambda k, t: None)
    assert len(seen) == 3 and "stream" not in json.loads(seen[2].content)   # 记住了,不再先试流式
    llm_client._NO_STREAM.clear()


def test_partial_string_reads_unfinished_json():
    assert partial_string('{"analysis": "先买那刻', "analysis") == "先买那刻"
    assert partial_string('{"analysis": "第一行\\n第二\\u884c", "actions": [', "analysis") == "第一行\n第二行"
    assert partial_string('{"analysis": "引号\\"和反斜杠\\', "analysis") == '引号"和反斜杠'
    assert partial_string('{"analysis": "\\u884', "analysis") == ""          # \u 还没收全
    assert partial_string('{"actions": []', "analysis") is None


def test_partial_strings_reads_unfinished_list():
    from cwagent.orchestrator.pending import partial_strings

    src = '{"analysis": "x", "alternatives": ["转反击:克拉拉\\n只有 1 星", "继续刷'
    assert partial_strings(src, "alternatives") == ["转反击:克拉拉\n只有 1 星", "继续刷"]
    assert partial_strings('{"alternatives": [], "actions": [', "alternatives") == []
    assert partial_strings('{"alternatives": ["a", "b"], "actions": [{"type": "x"', "alternatives") == ["a", "b"]
    assert partial_strings('{"analysis": "x"', "alternatives") is None


def test_pending_file_follows_the_stream_and_goes_away(tmp_path, monkeypatch):
    import cwagent.orchestrator.pending as pending

    monkeypatch.setattr(pending, "WRITE_GAP", 0)
    path = tmp_path / "pending.json"
    p = PendingDecision(path, {"step": 7, "screen": "prepare", "node": "1-4"})
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["step"] == 7 and first["analysis"] is None and first["reasoning"] == ""
    p.feed("reset", "")
    p.feed("reasoning", "金币够升 5 级")
    p.feed("tool", '{"analysis": "先升级再')
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reasoning"] == "金币够升 5 级" and data["analysis"] == "先升级再" and not data["acting"]
    assert data["alternatives"] is None
    p.feed("tool", '补人", "alternatives": ["存钱吃利')
    assert json.loads(path.read_text(encoding="utf-8"))["alternatives"] == ["存钱吃利"]
    p.feed("tool", '息"], "actions": [')
    p.feed("reset", "")                          # 输出不合法重问
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["retries"] == 1 and data["reasoning"] == "" and data["analysis"] is None
    p.close()
    assert not path.exists()
    p.feed("reasoning", "关掉之后不再写")
    assert not path.exists()


def this_process() -> dict:
    """meta.json 里记下的跑对局的进程:就是测试进程自己(一直在)。"""
    import os

    from cwagent import proc

    return {"pid": os.getpid(), "pid_started": proc.started(os.getpid())}


def dead_pid() -> int:
    """一个已经退出的进程号(像是崩掉的对局进程)。"""
    import subprocess
    import sys

    return int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True, check=True).stdout)


def test_run_store_serves_pending_only_while_running(tmp_path):
    from cwagent.server.runs import RunStore

    run = tmp_path / "20261118-094054_m"
    run.mkdir()
    (run / "steps.jsonl").write_text(json.dumps({"step": 6, "screen": "prepare"}) + "\n", encoding="utf-8")
    (run / "meta.json").write_text(json.dumps(this_process()), encoding="utf-8")
    store = RunStore(tmp_path)
    assert store.pending(run.name) is None and store.summary(run)["live"]
    p = PendingDecision(run / "pending.json", {"step": 7})
    assert store.pending(run.name)["step"] == 7
    # 很久没写记录(一场战斗最长约 20 分钟)也还在进行
    import os
    old = time.time() - 1800
    os.utime(run / "pending.json", (old, old))
    os.utime(run / "steps.jsonl", (old, old))
    assert store.pending(run.name)["step"] == 7 and store.summary(run)["live"]
    # 进程崩掉留下的:不算决策中,对局也不算进行中
    (run / "meta.json").write_text(json.dumps({"pid": dead_pid()}), encoding="utf-8")
    assert store.pending(run.name) is None and not store.summary(run)["live"]
    # 进程号被别的进程重用了(启动时间对不上)也不算;正常结束写了结束时间的不算
    (run / "meta.json").write_text(json.dumps({**this_process(), "pid_started": 1.0}), encoding="utf-8")
    import sys
    assert store.summary(run)["live"] is (sys.platform != "win32")   # 非 Windows 只能看进程在不在
    (run / "meta.json").write_text(json.dumps({**this_process(), "ended_at": "x"}), encoding="utf-8")
    assert store.pending(run.name) is None and not store.summary(run)["live"]
    p.close()


def test_runner_shows_the_step_while_the_model_is_deciding(tmp_path, monkeypatch):
    """开始问模型时 pending.json 就有这一步(编号、界面、模型看的标号图),流式片段跟着写进去,决策完删掉。"""
    from cwagent.brain.screen_agent import Action
    from cwagent.orchestrator.loop import RunOptions, Runner, Trace
    from tests.test_loop import FakeBrain, FakeOcr, Sim
    import cwagent.orchestrator.loop as loop
    import cwagent.orchestrator.pending as pending

    monkeypatch.setattr(loop, "ACTION_GAP", 0)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    monkeypatch.setattr(pending, "WRITE_GAP", 0)
    pages = {"env": [("投资环境", 0.5, 0.09), ("追击概念股", 0.77, 0.36), ("确认", 0.56, 0.91)],
             "end": [("对局评价", 0.5, 0.3)]}
    sim = Sim(pages, "env", lambda state, a: {("env", ("click", 0.56, 0.91)): "end"}.get((state, a)))
    seen = []

    class Brain(FakeBrain):
        def decide(self, ep, text, recalled=None, on_delta=None):
            on_delta("reset", "")
            on_delta("tool", '{"analysis": "追击概念股适合')
            path = tmp_path / "run" / "pending.json"
            on_delta("tool", '那刻夏"')
            seen.append(json.loads(path.read_text(encoding="utf-8")))
            return super().decide(ep, text, recalled)

    brain = Brain({"environment_choice": [[Action(type="click_text", text="追击概念股"), Action(type="click_text", text="确认")]]})
    runner = Runner(sim, brain, Trace(tmp_path / "run"), ocr=FakeOcr(sim), log=lambda m: None, settle=False)
    runner.run(RunOptions(max_steps=5))
    step = json.loads((tmp_path / "run" / "steps.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert seen[0]["step"] == step["step"] and seen[0]["screen"] == "environment_choice"
    assert seen[0]["vision"] == step["vision"] and seen[0]["analysis"] == "追击概念股适合那刻夏"
    assert not (tmp_path / "run" / "pending.json").exists()
