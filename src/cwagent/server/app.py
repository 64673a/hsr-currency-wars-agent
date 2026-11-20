"""后端接口(FastAPI)。设计见 docs/TECH_DESIGN.md 第 13 节。

提供:状态、对局控制与记录、实时画面信令、知识库浏览与同步、厂商与模型档案管理、运行参数、记忆、外部 agent 接入(/mcp)。前端构建产物(web/dist)由这里托管。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.routing import Route

from .. import __version__, paths
from ..config import (
    AgentSettings,
    ConfigError,
    ConfigStore,
    Effort,
    KnowledgeSettings,
    ModelProfile,
    OcrSettings,
    ProviderConfig,
    Roles,
    ServerSettings,
    mask_key,
)
from ..knowledge import KIND_NAMES, KINDS, KnowledgeDB
from ..memory import Cues, Memory, MemoryError, MemoryStore, format_for_prompt, recall
from ..stream import LiveStream
from ..vision.adapters import list_adapters
from ..vision.adapters import pick as pick_adapter
from ..vision.ocr import Ocr, directml_available, sample_frame
from ..vision.ocr import configure as ocr_configure
from .jobs import Job
from .mcp import create_mcp
from .runs import PlayController, RunStore

# 知识库类别 -> 模板图标目录
ICON_DIRS = {"roles": "roles", "traits": "traits", "equipment": "equipment", "augments": "augments",
             "portals": "portals", "spotlights": "spotlights"}


class ProviderOut(BaseModel):
    name: str
    format: str
    base_url: str
    api_key: str           # 掩码
    opencode_go: bool | None
    has_key: bool
    extra_headers: dict[str, str]
    anthropic_version: str
    models: list[str]      # 使用该厂商的模型档案名


class SettingsIn(BaseModel):
    roles: Roles | None = None
    agent: AgentSettings | None = None
    server: ServerSettings | None = None
    knowledge: KnowledgeSettings | None = None
    ocr: OcrSettings | None = None


class SyncIn(BaseModel):
    max_lineups: int | None = None
    templates: bool = True


class StatusIn(BaseModel):
    status: str


class PlayIn(BaseModel):
    model: str | None = None
    effort: Effort | None = None   # 不填用档案的设置
    difficulty: str | None = None
    max_steps: int = 700
    rounds: int = Field(1, ge=1, le=50)


class RunsDeleteIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class PreviewIn(BaseModel):
    entities: list[str] = []
    texts: list[str] = []
    nodes: list[str] = []
    screen: str | None = None
    layer: str | None = None
    include_draft: bool = False


def _bad(e: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(e))


def create_app(*, config_store: ConfigStore | None = None, knowledge_path: Path | None = None,
               memory_store: MemoryStore | None = None, templates_dir: Path | None = None,
               web_dist: Path | None = None, sync_fn: Callable[..., Any] | None = None,
               runs_dir: Path | None = None, device_factory: Callable[[], Any] | None | str = "default",
               live: LiveStream | None = None) -> FastAPI:
    store = config_store or ConfigStore()
    kdb = KnowledgeDB(knowledge_path or paths.knowledge_db())
    memories = memory_store or MemoryStore()
    tpl_dir = templates_dir or paths.templates_dir()
    dist = web_dist or paths.web_dist()
    sync_job = Job("knowledge-sync")
    runs = RunStore(runs_dir or paths.runs_dir())
    player = PlayController(store, runs, device_factory)
    if sync_fn is None:
        from ..knowledge.sync import sync as sync_fn  # noqa: PLW0127

    mcp_app, mcp_running = create_mcp(player)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_running():
            yield

    app = FastAPI(title="货币战争 agent", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    # 外部 agent 接入(11.9);要排在最后的网页兜底路由前面
    app.router.routes.append(Route("/mcp", endpoint=mcp_app))

    def load_config():
        try:
            return store.load()
        except (ConfigError, ValueError) as e:
            raise HTTPException(500, f"config.yaml 有误:{e}") from None

    try:   # 网页的只读截图等不经过对局的识别也按配置选设备
        _cfg = store.load()
        ocr_configure(_cfg.ocr.device, _cfg.ocr.adapter)
    except (ConfigError, ValueError):
        pass

    # ------------------------------------------------------------ 状态

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        cfg = load_config()
        return {"version": __version__, "knowledge": kdb.meta(), "sync": {"running": sync_job.status()["running"]},
                "providers": len(cfg.providers), "models": len(cfg.models), "run": player.status()}

    # ------------------------------------------------------------ 对局

    @app.get("/api/play")
    def play_status() -> dict[str, Any]:
        return player.status()

    @app.post("/api/play/start")
    def play_start(body: PlayIn) -> dict[str, Any]:
        try:
            return player.start(model=body.model, effort=body.effort, difficulty=body.difficulty,
                                max_steps=body.max_steps, rounds=body.rounds)
        except (RuntimeError, ConfigError) as e:
            raise _bad(e, 409) from None

    @app.post("/api/play/stop")
    def play_stop() -> dict[str, Any]:
        return player.stop()

    # ------------------------------------------------------------ 实时画面(WebRTC,WHEP 信令转发给本机的 MediaMTX)

    @app.get("/api/live")
    def live_status() -> dict[str, Any]:
        if live is None:
            return {"available": False, "reason": "没有启动实时画面服务", "encoder": None}
        return live.status()

    @app.post("/api/live/whep", status_code=201)
    async def live_offer(request: Request) -> Response:
        if live is None or not live.running():
            raise HTTPException(503, "实时画面不可用")
        result = await live.offer((await request.body()).decode())
        if result.status != 201:
            raise HTTPException(result.status, result.error)
        headers = {"Location": f"/api/live/whep/{result.session}"} if result.session else {}
        return Response(result.answer, status_code=201, media_type="application/sdp", headers=headers)

    @app.delete("/api/live/whep/{session}", status_code=204)
    async def live_close(session: str) -> None:
        if live is not None:
            await live.close(session)

    @app.get("/api/runs")
    def run_list() -> list[dict[str, Any]]:
        return runs.list()

    @app.post("/api/runs/delete")
    def run_delete(body: RunsDeleteIn) -> dict[str, Any]:
        current = player.status()
        keep = {current["run_id"]} if current["running"] and current["run_id"] else set()
        return runs.delete(body.ids, keep=keep)

    @app.get("/api/runs/{run_id}")
    def run_get(run_id: str) -> dict[str, Any]:
        try:
            return runs.summary(runs.dir(run_id))
        except KeyError:
            raise HTTPException(404, f"对局 {run_id} 不存在") from None

    @app.get("/api/runs/{run_id}/steps")
    def run_steps(run_id: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)) -> list[dict[str, Any]]:
        try:
            return runs.steps(run_id, after=after, limit=limit)
        except KeyError:
            raise HTTPException(404, f"对局 {run_id} 不存在") from None

    @app.get("/api/runs/{run_id}/pending")
    def run_pending(run_id: str) -> dict[str, Any]:
        """模型决策中的那一步;没有在决策时返回空对象。"""
        try:
            return runs.pending(run_id) or {}
        except KeyError:
            raise HTTPException(404, f"对局 {run_id} 不存在") from None

    @app.get("/api/runs/{run_id}/shots/{name}")
    def run_shot(run_id: str, name: str) -> FileResponse:
        try:
            return FileResponse(runs.shot(run_id, name), headers={"Cache-Control": "max-age=86400"})
        except KeyError:
            raise HTTPException(404, "截图不存在") from None

    # ------------------------------------------------------------ 知识库
    @app.get("/api/runs/{run_id}/vision/{name}")
    def run_vision(run_id: str, name: str) -> FileResponse:
        try:
            return FileResponse(runs.shot(run_id, name, folder="vision"), headers={"Cache-Control": "max-age=86400"})
        except KeyError:
            raise HTTPException(404, "模型输入截图不存在") from None

    def icon_url(kind: str, entry_id: str, remote: str = "") -> str:
        folder = ICON_DIRS.get(kind)
        if folder and (tpl_dir / folder / f"{entry_id}.png").exists():
            return f"/templates/{folder}/{entry_id}.png"
        return remote

    @app.get("/api/knowledge/meta")
    def knowledge_meta() -> dict[str, Any]:
        return {"meta": kdb.meta(), "kinds": [{"kind": k, "name": KIND_NAMES[k]} for k in KINDS]}

    @app.get("/api/knowledge/sync")
    def knowledge_sync_status() -> dict[str, Any]:
        return sync_job.status()

    @app.post("/api/knowledge/sync", status_code=202)
    def knowledge_sync(body: SyncIn | None = None) -> dict[str, Any]:
        body = body or SyncIn()
        settings = load_config().knowledge
        max_lineups = settings.max_lineups if body.max_lineups is None else body.max_lineups

        def run(progress):
            return sync_fn(max_lineups=max_lineups, interval=settings.request_interval,
                           templates=body.templates, progress=progress)

        if not sync_job.start(run):
            raise HTTPException(409, "同步正在进行中")
        return sync_job.status()

    @app.get("/api/knowledge/{kind}")
    def knowledge_list(kind: str, q: str = "", limit: int = Query(100, ge=1, le=1000),
                       offset: int = Query(0, ge=0)) -> dict[str, Any]:
        if kind not in KINDS:
            raise HTTPException(404, f"没有这类条目:{kind}")
        if not kdb.exists():
            return {"total": 0, "items": [], "synced": False}
        total, items = kdb.list(kind, q=q, limit=limit, offset=offset)
        for item in items:
            # 本机没下载模板图标时用官方图片地址
            item["icon"] = icon_url(kind, item["id"]) or (kdb.get(kind, item["id"]) or {}).get("icon", "")
        return {"total": total, "items": items, "synced": True}

    @app.get("/api/knowledge/{kind}/{entry_id}")
    def knowledge_get(kind: str, entry_id: str) -> dict[str, Any]:
        if kind not in KINDS:
            raise HTTPException(404, f"没有这类条目:{kind}")
        if not kdb.exists():
            raise HTTPException(404, "知识库还没有同步")
        data = kdb.get(kind, entry_id)
        if data is None:
            raise HTTPException(404, f"{KIND_NAMES[kind]} {entry_id} 不存在")
        data["icon"] = icon_url(kind, entry_id, data.get("icon", ""))
        return data

    # ------------------------------------------------------------ 厂商

    def provider_out(p: ProviderConfig, cfg) -> ProviderOut:
        return ProviderOut(name=p.name, format=p.format, base_url=p.base_url, api_key=mask_key(p.api_key),
                           opencode_go=p.opencode_go,
                           has_key=bool(p.api_key), extra_headers=p.extra_headers,
                           anthropic_version=p.anthropic_version,
                           models=[m.name for m in cfg.models if m.provider == p.name])

    @app.get("/api/providers")
    def providers() -> list[ProviderOut]:
        cfg = load_config()
        return [provider_out(p, cfg) for p in cfg.providers]

    @app.post("/api/providers", status_code=201)
    def provider_create(body: ProviderConfig) -> ProviderOut:
        try:
            store.upsert_provider(body)
        except ConfigError as e:
            raise _bad(e) from None
        cfg = load_config()
        return provider_out(cfg.provider(body.name), cfg)

    @app.put("/api/providers/{name}")
    def provider_update(name: str, body: ProviderConfig) -> ProviderOut:
        try:
            store.upsert_provider(body, original_name=name)
        except ConfigError as e:
            raise _bad(e, 404 if "不存在" in str(e) else 400) from None
        cfg = load_config()
        return provider_out(cfg.provider(body.name), cfg)

    @app.delete("/api/providers/{name}", status_code=204)
    def provider_delete(name: str) -> None:
        try:
            store.delete_provider(name)
        except ConfigError as e:
            raise _bad(e, 404 if "不存在" in str(e) else 400) from None

    @app.get("/api/providers/{name}/models")
    def provider_models(name: str) -> list[dict[str, Any]]:
        from ..llm import LLMError, list_models

        provider = load_config().provider(name)
        if provider is None:
            raise HTTPException(404, f"厂商 {name} 不存在")
        try:
            return [m.model_dump() for m in list_models(provider)]
        except LLMError as e:
            raise HTTPException(502, f"拉取模型列表失败:{e}") from None

    # ------------------------------------------------------------ 模型档案

    @app.get("/api/models")
    def models() -> list[ModelProfile]:
        return load_config().models

    @app.post("/api/models", status_code=201)
    def model_create(body: ModelProfile) -> ModelProfile:
        try:
            return store.upsert_model(body)
        except ConfigError as e:
            raise _bad(e) from None

    @app.put("/api/models/{name}")
    def model_update(name: str, body: ModelProfile) -> ModelProfile:
        try:
            return store.upsert_model(body, original_name=name)
        except ConfigError as e:
            raise _bad(e, 404 if f"模型档案 {name} 不存在" in str(e) else 400) from None

    @app.delete("/api/models/{name}", status_code=204)
    def model_delete(name: str) -> None:
        try:
            store.delete_model(name)
        except ConfigError as e:
            raise _bad(e, 404) from None

    @app.post("/api/models/{name}/test")
    def model_test(name: str) -> dict[str, Any]:
        from ..llm import check_profile

        try:
            endpoint = load_config().endpoint(name)
        except ConfigError as e:
            raise _bad(e, 404) from None
        return check_profile(endpoint)

    # ------------------------------------------------------------ 运行参数

    def settings_out(cfg) -> dict[str, Any]:
        return {"roles": cfg.roles, "agent": cfg.agent, "server": cfg.server, "knowledge": cfg.knowledge,
                "ocr": cfg.ocr}

    @app.get("/api/config")
    def config_get() -> dict[str, Any]:
        return settings_out(load_config())

    @app.put("/api/config")
    def config_put(body: SettingsIn) -> dict[str, Any]:
        try:
            cfg = store.update_settings(roles=body.roles, agent=body.agent, server=body.server,
                                        knowledge=body.knowledge, ocr=body.ocr)
        except ConfigError as e:
            raise _bad(e) from None
        ocr_configure(cfg.ocr.device, cfg.ocr.adapter)
        return settings_out(cfg)

    # ------------------------------------------------------------ 文字识别

    @app.get("/api/ocr")
    def ocr_info() -> dict[str, Any]:
        """可选的显卡(能用 DirectML 时才有)和自动会选哪块。"""
        adapters = list_adapters() if directml_available() else []
        auto = pick_adapter(adapters, None)
        return {"adapters": [{"name": a.name, "memory_mb": a.memory_mb} for a in adapters],
                "auto": auto.name if auto else None}

    @app.post("/api/ocr/test")
    def ocr_test(body: OcrSettings) -> dict[str, Any]:
        """按给的设置识别一张 1920x1080 的测试画面:实际用的设备、初始化用时、整屏一次的用时。"""
        started = time.perf_counter()
        ocr = Ocr(device=body.device, adapter=body.adapter)
        backend = ocr.backend
        init_s = time.perf_counter() - started
        frame = sample_frame()
        ocr(frame)
        times = []
        for _ in range(3):
            t = time.perf_counter()
            ocr(frame)
            times.append(time.perf_counter() - t)
        return {"backend": backend, "ms": round(sorted(times)[1] * 1000), "init_s": round(init_s, 1)}

    # ------------------------------------------------------------ 记忆

    def memory_out(m: Memory, where: str) -> dict[str, Any]:
        return {**m.model_dump(mode="json"), "origin": where}

    @app.get("/api/memories")
    def memory_list(status: str | None = None) -> list[dict[str, Any]]:
        return [memory_out(m, w) for m, w in memories.all() if status is None or m.status == status]

    def memory_in(body: dict[str, Any]) -> Memory:
        body = {k: v for k, v in body.items() if k != "origin"}  # 前端会把读到的条目原样提交回来
        try:
            return Memory.model_validate(body)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/memories", status_code=201)
    def memory_create(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return memory_out(memories.add(memory_in(body)), "本机")
        except MemoryError as e:
            raise _bad(e) from None

    @app.put("/api/memories/{memory_id}")
    def memory_update(memory_id: str, body: dict[str, Any]) -> dict[str, Any]:
        memory = memory_in(body)
        if memories.get(memory_id) is None:
            raise HTTPException(404, f"记忆 {memory_id} 不存在")
        if memory.id != memory_id:
            raise HTTPException(400, "不能修改记忆的 id")
        return memory_out(memories.save(memory), "本机")

    @app.post("/api/memories/{memory_id}/status")
    def memory_status(memory_id: str, body: StatusIn) -> dict[str, Any]:
        if body.status not in ("draft", "approved", "rejected", "archived"):
            raise HTTPException(400, f"未知状态:{body.status}")
        try:
            return memory_out(memories.set_status(memory_id, body.status), "本机")
        except MemoryError as e:
            raise _bad(e, 404) from None

    @app.post("/api/memories/preview")
    def memory_preview(body: PreviewIn) -> dict[str, Any]:
        cues = Cues(entities=set(body.entities), texts=body.texts, nodes=set(body.nodes), screen=body.screen,
                    layer=body.layer)
        hits = recall([m for m, _ in memories.all()], cues, include_draft=body.include_draft)
        return {"items": [{"id": r.memory.id, "title": r.memory.title, "score": r.score, "reasons": r.reasons}
                          for r in hits],
                "prompt": format_for_prompt(hits)}

    # ------------------------------------------------------------ 静态文件

    tpl_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/templates", StaticFiles(directory=tpl_dir), name="templates")

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> None:
        raise HTTPException(404, f"没有这个接口:/api/{rest}")

    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")

        @app.get("/{rest:path}", include_in_schema=False)
        def spa(rest: str) -> FileResponse:
            target = (dist / rest).resolve()
            if rest and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend() -> dict[str, str]:
            return {"message": "前端还没有构建:cd web && npm install && npm run build。接口文档见 /api/docs"}

    return app
