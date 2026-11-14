import time

import pytest
from fastapi.testclient import TestClient

from cwagent.config import ConfigStore
from cwagent.knowledge.build import build
from cwagent.knowledge.db import write
from cwagent.memory import MemoryStore
from cwagent.server import create_app
from tests.fixtures.official_sample import config, lineup


@pytest.fixture
def env(tmp_path):
    kpath = tmp_path / "knowledge.sqlite"
    write(kpath, build(config(), [lineup()]), {})
    tpl = tmp_path / "templates"
    (tpl / "roles").mkdir(parents=True)
    (tpl / "roles" / "1001.png").write_bytes(b"png")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    calls = []

    def fake_sync(**kw):
        calls.append(kw)
        kw["progress"]("config", "拉取静态配置")
        return {"counts": {"roles": 2}}

    store = ConfigStore(tmp_path / "config.yaml")
    app = create_app(config_store=store, knowledge_path=kpath, templates_dir=tpl, web_dist=dist,
                     memory_store=MemoryStore(tmp_path / "memories.yaml", tmp_path / "memory.sqlite"),
                     sync_fn=fake_sync)
    return TestClient(app), store, calls


def test_status_and_knowledge(env):
    client, _, _ = env
    s = client.get("/api/status").json()
    assert s["knowledge"]["season_id"] == "2" and s["providers"] == 0
    kinds = client.get("/api/knowledge/meta").json()["kinds"]
    assert {"kind": "roles", "name": "角色"} in kinds

    data = client.get("/api/knowledge/roles", params={"q": "甲"}).json()
    assert data["total"] == 1 and data["items"][0]["icon"] == "/templates/roles/1001.png"
    assert client.get("/api/knowledge/roles/1002").json()["icon"].startswith("https://")  # 没有本地图标时用远程地址
    assert client.get("/api/knowledge/traits/200402").json()["layers"][0]["count"] == 2
    assert client.get("/api/knowledge/nope").status_code == 404
    assert client.get("/api/knowledge/roles/9999").status_code == 404
    assert client.get("/templates/roles/1001.png").content == b"png"


def test_sync_job(env):
    client, store, calls = env
    assert client.post("/api/knowledge/sync", json={"max_lineups": 5}).status_code == 202
    for _ in range(50):
        state = client.get("/api/knowledge/sync").json()
        if not state["running"]:
            break
        time.sleep(0.02)
    assert state["result"] == {"counts": {"roles": 2}} and state["error"] is None
    assert any("拉取静态配置" in line for line in state["log"])
    assert calls[0]["max_lineups"] == 5 and calls[0]["templates"] is True


def test_providers_and_models(env, monkeypatch):
    client, store, _ = env
    body = {"name": "ds", "format": "openai", "base_url": "https://api.example.com/", "api_key": "sk-1234567890abc"}
    r = client.post("/api/providers", json=body)
    assert r.status_code == 201 and r.json()["api_key"] == "sk-1…0abc" and r.json()["has_key"]
    assert client.post("/api/providers", json=body).status_code == 400  # 重名
    assert client.post("/api/providers", json={**body, "name": "x", "format": "nope"}).status_code == 422

    # 提交掩码表示沿用原 key
    r = client.put("/api/providers/ds", json={**body, "api_key": "sk-1…0abc", "base_url": "https://b.example.com"})
    assert r.status_code == 200 and store.load().provider("ds").api_key == "sk-1234567890abc"
    assert client.put("/api/providers/nope", json=body).status_code == 404

    m = {"name": "flash", "provider": "ds", "model": "flash", "extra_body": {"reasoning_effort": "high"}}
    assert client.post("/api/models", json=m).status_code == 201
    assert client.post("/api/models", json={**m, "name": "y", "provider": "nope"}).status_code == 400
    assert client.get("/api/providers").json()[0]["models"] == ["flash"]
    assert client.delete("/api/providers/ds").status_code == 400  # 仍被使用

    r = client.put("/api/config", json={"roles": {"tactical": "flash"}, "agent": {"use_guides": False}})
    assert r.status_code == 200 and r.json()["roles"]["tactical"] == "flash" and not r.json()["agent"]["use_guides"]
    assert client.put("/api/config", json={"roles": {"tactical": "nope"}}).status_code == 400

    r = client.put("/api/models/flash", json={**m, "name": "flash2"})
    assert r.status_code == 200 and client.get("/api/config").json()["roles"]["tactical"] == "flash2"

    # 连通性测试和模型列表:替换掉真实网络请求
    monkeypatch.setattr("cwagent.llm.check_profile", lambda ep: {"ok": True, "model": ep.model})
    assert client.post("/api/models/flash2/test").json() == {"ok": True, "model": "flash"}
    assert client.post("/api/models/nope/test").status_code == 404

    from cwagent.llm import LLMError, ModelInfo
    monkeypatch.setattr("cwagent.llm.list_models", lambda p: [ModelInfo(id="flash"), ModelInfo(id="pro")])
    assert [x["id"] for x in client.get("/api/providers/ds/models").json()] == ["flash", "pro"]

    def boom(p):
        raise LLMError("not found", status=404)
    monkeypatch.setattr("cwagent.llm.list_models", boom)
    assert client.get("/api/providers/ds/models").status_code == 502

    assert client.delete("/api/models/flash2").status_code == 204
    assert client.delete("/api/providers/ds").status_code == 204
    assert client.get("/api/providers").json() == []


def test_memories(env):
    client, _, _ = env
    body = {"kind": "打法", "title": "绯英叠层", "content": "奖励节点单挂", "status": "approved",
            "triggers": {"entities": ["绯英"], "nodes": ["reward"]}}
    created = client.post("/api/memories", json=body).json()
    assert created["origin"] == "本机" and created["id"].startswith("m-")
    r = client.post("/api/memories/preview", json={"entities": ["绯英"], "layer": "战术"}).json()
    assert r["items"][0]["title"] == "绯英叠层" and "绯英叠层" in r["prompt"]
    rejected = client.post(f"/api/memories/{created['id']}/status", json={"status": "rejected"}).json()
    assert rejected["status"] == "rejected"
    assert client.post("/api/memories/preview", json={"entities": ["绯英"]}).json()["items"] == []
    assert client.post(f"/api/memories/{created['id']}/status", json={"status": "bad"}).status_code == 400
    updated = client.put(f"/api/memories/{created['id']}", json={**rejected, "title": "新标题"}).json()
    assert updated["title"] == "新标题"
    assert len(client.get("/api/memories", params={"status": "rejected"}).json()) == 1
    assert client.post("/api/memories", json={"kind": "瞎写", "title": "x", "content": "y"}).status_code == 422


def test_spa_and_unknown_api(env):
    client, _, _ = env
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/settings").text == "<html>app</html>"  # 前端路由
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/api/nope").status_code == 404
    assert client.get("/../config.yaml").status_code in (200, 404) and "api_key" not in client.get("/../config.yaml").text


def test_without_knowledge(tmp_path):
    app = create_app(config_store=ConfigStore(tmp_path / "c.yaml"), knowledge_path=tmp_path / "none.sqlite",
                     templates_dir=tmp_path / "tpl", web_dist=tmp_path / "nodist",
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"))
    client = TestClient(app)
    assert client.get("/api/knowledge/roles").json() == {"total": 0, "items": [], "synced": False}
    assert client.get("/api/status").json()["knowledge"] == {}
    assert "前端还没有构建" in client.get("/").json()["message"]



def test_runs_and_play(tmp_path):
    import json

    runs = tmp_path / "runs"
    d = runs / "20261114-092016_deepseek-flash"
    (d / "shots").mkdir(parents=True)
    (d / "shots" / "0001.jpg").write_bytes(b"jpg")
    rows = [
        {"step": 1, "screen": "prepare", "shot": "shots/0001.jpg", "texts": ["(0.245,0.072) 1-9", "(0.5,0.5) 出战"],
         "decision": {"analysis": "出战"}, "actions": ["点击「出战」"]},
        {"step": 2, "screen": "other", "texts": ["(0.245,0.07) 1-30%", "(0.5,0.2) 挑战失败"], "actions": []},
        {"step": 3, "screen": "settlement", "node": None, "texts": ["(0.5,0.2) 挑战失败"], "actions": []},
    ]
    (d / "steps.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n{\"step\": 4",
                                   encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"profile": "deepseek-flash", "ended_at": "x", "result": "settlement"}),
                                 encoding="utf-8")
    app = create_app(config_store=ConfigStore(tmp_path / "c.yaml"), knowledge_path=tmp_path / "none.sqlite",
                     templates_dir=tmp_path / "tpl", web_dist=tmp_path / "dist", runs_dir=runs,
                     memory_store=MemoryStore(tmp_path / "m.yaml", tmp_path / "m.sqlite"), device_factory=None)
    client = TestClient(app)

    [run] = client.get("/api/runs").json()
    assert run["id"] == d.name and run["steps"] == 3 and run["node"] == "1-9"
    assert run["outcome"] == "失败" and run["decisions"] == 1 and run["live"] is False
    steps = client.get(f"/api/runs/{d.name}/steps", params={"after": 0}).json()
    assert [s["step"] for s in steps] == [1, 2, 3] and steps[0]["node"] == "1-9" and steps[1]["node"] is None
    assert client.get(f"/api/runs/{d.name}/steps", params={"after": 1}).json()[0]["step"] == 2
    d0 = runs / "20261114-100000_x"
    d0.mkdir()
    leftover = {"step": 1, "screen": "settlement", "texts": ["(0.5,0.2) 挑战失败"], "actions": []}  # 上一局留下的结算界面
    (d0 / "steps.jsonl").write_text(json.dumps(leftover, ensure_ascii=False) + "\n", encoding="utf-8")
    assert next(r for r in client.get("/api/runs").json() if r["id"] == d0.name)["outcome"] is None
    assert client.get(f"/api/runs/{d.name}/shots/0001.jpg").content == b"jpg"
    assert client.get(f"/api/runs/{d.name}/shots/..%2Fmeta.json").status_code == 404
    assert client.get("/api/runs/nope/steps").status_code == 404

    play = client.get("/api/play").json()
    assert play["available"] is False and play["running"] is False
    assert client.post("/api/play/start", json={}).status_code == 409
