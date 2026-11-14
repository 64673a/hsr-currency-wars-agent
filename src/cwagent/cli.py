"""命令行入口:uv run cwagent <命令>。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from .config import ConfigError, ConfigStore, ModelProfile, ProviderConfig, mask_key

app = typer.Typer(help="货币战争 LLM 游玩 agent", no_args_is_help=True, add_completion=False)
providers_app = typer.Typer(help="厂商(接口地址 + key)", no_args_is_help=True)
models_app = typer.Typer(help="模型档案(厂商 + 模型 id + 参数)", no_args_is_help=True)
app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
knowledge_app = typer.Typer(help="知识库(官方数据)", no_args_is_help=True)
app.add_typer(knowledge_app, name="knowledge")
regions_app = typer.Typer(help="界面区域坐标(config/regions.yaml)", no_args_is_help=True)
app.add_typer(regions_app, name="regions")
memory_app = typer.Typer(help="记忆(积累的游戏经验)", no_args_is_help=True)
app.add_typer(memory_app, name="memory")
companions_app = typer.Typer(help="奇遇伙伴档案(边玩边完善)", no_args_is_help=True)
app.add_typer(companions_app, name="companions")
stream_app = typer.Typer(help="网页上的实时画面", no_args_is_help=True)
app.add_typer(stream_app, name="stream")
web_app = typer.Typer(help="网页界面", no_args_is_help=True)
app.add_typer(web_app, name="web")


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _json_option(value: str | None, what: str) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except ValueError as e:
        raise typer.BadParameter(f"{what} 不是合法 JSON:{e}") from None
    if not isinstance(data, dict):
        raise typer.BadParameter(f"{what} 必须是 JSON 对象")
    return data


# ---------------------------------------------------------------- 厂商

@providers_app.command("list")
def providers_list() -> None:
    """列出厂商。"""
    store = ConfigStore()
    cfg = store.load()
    typer.echo(f"配置文件: {store.path}")
    if not cfg.providers:
        typer.echo("(没有厂商,用 `cwagent providers add` 添加)")
    for p in cfg.providers:
        used = [m.name for m in cfg.models if m.provider == p.name]
        typer.echo(f"- {p.name}: {p.format}  {p.base_url}  key={mask_key(p.api_key) or '未填'}"
                   f"  模型档案: {', '.join(used) or '无'}")


@providers_app.command("add")
def providers_add(
    name: str = typer.Argument(..., help="厂商名,自己起"),
    format: str = typer.Option(..., "--format", "-f", help="接口格式:openai 或 anthropic"),
    base_url: str = typer.Option(..., "--base-url", "-u", help="接口地址"),
    api_key: str = typer.Option("", "--api-key", "-k", help="API key;不填则稍后在 config.yaml 或网页里填"),
) -> None:
    """添加厂商。"""
    try:
        ConfigStore().upsert_provider(ProviderConfig(name=name, format=format, base_url=base_url, api_key=api_key))
    except (ConfigError, ValueError) as e:
        _fail(str(e))
    typer.echo(f"已添加厂商 {name}。查看可用模型:cwagent providers models {name}")


@providers_app.command("models")
def providers_models(name: str = typer.Argument(..., help="厂商名")) -> None:
    """从厂商接口拉取可用模型列表。"""
    from .llm import LLMError, list_models

    provider = ConfigStore().load().provider(name)
    if provider is None:
        _fail(f"厂商 {name} 不存在")
    try:
        models = list_models(provider)
    except LLMError as e:
        _fail(f"拉取模型列表失败:{e}\n(该接口可能不提供模型列表,可以直接用 `cwagent models add --model <id>` 手动填写)")
    typer.echo(f"{name} 共 {len(models)} 个模型:")
    for m in models:
        extra = "  ".join(x for x in (m.display_name, m.owned_by,
                                      f"上下文 {m.context_window}" if m.context_window else "") if x)
        typer.echo(f"  {m.id}" + (f"  ({extra})" if extra else ""))


# ---------------------------------------------------------------- 模型档案

@models_app.command("list")
def models_list() -> None:
    """列出模型档案和各决策层的选择。"""
    cfg = ConfigStore().load()
    if not cfg.models:
        typer.echo("(没有模型档案,用 `cwagent models add <厂商>` 添加)")
    for m in cfg.models:
        extra = f"  extra_body={json.dumps(m.extra_body, ensure_ascii=False)}" if m.extra_body else ""
        typer.echo(f"- {m.name}: {m.provider} / {m.model}  tool_mode={m.tool_mode}{extra}")
    roles = cfg.roles.model_dump()
    default = cfg.models[0].name if cfg.models else "-"
    typer.echo("决策层: " + ", ".join(f"{k}={v or default + '(默认)'}" for k, v in roles.items()))


@models_app.command("add")
def models_add(
    provider: str = typer.Argument(..., help="厂商名"),
    model: str = typer.Option(None, "--model", "-m", help="模型 id;不填则拉取列表后选择"),
    name: str = typer.Option(None, "--name", "-n", help="档案名,默认与模型 id 相同"),
    extra_body: str = typer.Option(None, "--extra-body", help='透传参数 JSON,如 \'{"reasoning_effort": "high"}\''),
    max_tokens: int = typer.Option(None, "--max-tokens"),
    tool_mode: str = typer.Option("native", "--tool-mode", help="native 或 json"),
) -> None:
    """为某个厂商添加模型档案。"""
    store = ConfigStore()
    cfg = store.load()
    prov = cfg.provider(provider)
    if prov is None:
        _fail(f"厂商 {provider} 不存在")
    if not model:
        from .llm import LLMError, list_models

        try:
            choices = [m.id for m in list_models(prov)]
        except LLMError as e:
            _fail(f"拉取模型列表失败:{e};请用 --model 手动指定模型 id")
        if not choices:
            _fail("模型列表为空;请用 --model 手动指定模型 id")
        for i, mid in enumerate(choices, 1):
            typer.echo(f"  {i:>3}. {mid}")
        idx = typer.prompt("选择模型编号", type=int)
        if not 1 <= idx <= len(choices):
            _fail("编号超出范围")
        model = choices[idx - 1]
    try:
        profile = ModelProfile(name=name or model, provider=provider, model=model, max_tokens=max_tokens,
                               extra_body=_json_option(extra_body, "--extra-body"), tool_mode=tool_mode)
        store.upsert_model(profile)
    except (ConfigError, ValueError) as e:
        _fail(str(e))
    typer.echo(f"已添加模型档案 {profile.name}({provider} / {model})。测试:cwagent models test {profile.name}")


@models_app.command("remove")
def models_remove(name: str = typer.Argument(..., help="档案名")) -> None:
    """删除模型档案。"""
    try:
        ConfigStore().delete_model(name)
    except ConfigError as e:
        _fail(str(e))
    typer.echo(f"已删除模型档案 {name}")


@models_app.command("test")
def models_test(names: Annotated[list[str] | None, typer.Argument(help="档案名,不填则测试全部")] = None) -> None:
    """发一个最小请求,检查档案能否调用、是否返回工具调用和思考内容。"""
    from .llm import check_profile

    cfg = ConfigStore().load()
    names = names or [m.name for m in cfg.models]
    if not names:
        _fail("还没有模型档案")
    failed = False
    for name in names:
        try:
            endpoint = cfg.endpoint(name)
        except ConfigError as e:
            typer.secho(f"✗ {name}: {e}", fg="red")
            failed = True
            continue
        result = check_profile(endpoint)
        if result["ok"]:
            u = result["usage"]
            typer.secho(f"✓ {name}: {result['latency_s']}s  model={result['model']}  "
                        f"stop={result['stop_reason']}  tokens 入/出={u['input_tokens']}/{u['output_tokens']}"
                        f"(缓存读 {u['cache_read_tokens']})", fg="green")
            args = json.dumps(result["tool_arguments"], ensure_ascii=False) if result["tool_arguments"] else ""
            typer.echo(f"    工具调用: {'有' if result['tool_call'] else '无'}  {args}")
            if result["reasoning"]:
                typer.echo(f"    思考内容: 有  {result['reasoning_preview'][:120]!r}")
            else:
                typer.echo("    思考内容: 本次未返回(请求很简单时模型可能直接作答;"
                           "需要思考内容时检查档案的 extra_body)")
            if result["text_preview"]:
                typer.echo(f"    文本: {result['text_preview'][:120]!r}")
            failed |= not result["tool_call"]
        else:
            failed = True
            typer.secho(f"✗ {name}: {result['error']}", fg="red")
            if result.get("body"):
                typer.echo(f"    {result['body'][:300]}")
    raise typer.Exit(1 if failed else 0)


# ---------------------------------------------------------------- 知识库

@knowledge_app.command("sync")
def knowledge_sync(
    max_lineups: Annotated[int | None, typer.Option(help="最多抓取的官方阵容数,默认取 config.yaml")] = None,
    templates: Annotated[bool, typer.Option(help="是否下载模板图标")] = True,
) -> None:
    """从米游社官方接口同步角色、羁绊、装备、投资策略、投资环境、主角光环和阵容。"""
    from .knowledge.official_api import OfficialApiError
    from .knowledge.sync import sync

    settings = ConfigStore().load().knowledge
    try:
        result = sync(max_lineups=settings.max_lineups if max_lineups is None else max_lineups,
                      interval=settings.request_interval, templates=templates,
                      progress=lambda stage, msg: typer.echo(f"[{stage}] {msg}"))
    except OfficialApiError as e:
        _fail(f"同步失败:{e}")
    m = result.meta
    typer.secho(f"同步完成:赛季 {m['season_id']}-{m['sub_season_id']},版本 {m['game_version']},"
                f"抓取阵容 {result.lineups_fetched} 条", fg="green")
    from .knowledge.build import KIND_NAMES
    typer.echo("  " + "  ".join(f"{KIND_NAMES[k]} {v}" for k, v in result.counts.items()))
    if templates:
        typer.echo(f"  新下载图标 {sum(result.templates.values())} 个" +
                   (f",失败 {len(result.template_errors)} 个" if result.template_errors else ""))
        for err in result.template_errors[:5]:
            typer.echo(f"    {err}")
    if result.warnings:
        typer.secho(f"  提示 {len(result.warnings)} 条:", fg="yellow")
        for w in result.warnings:
            typer.echo(f"    - {w}")


@knowledge_app.command("stats")
def knowledge_stats() -> None:
    """查看本地知识库的版本和条目数。"""
    from . import paths
    from .knowledge import KIND_NAMES, KnowledgeDB

    meta = KnowledgeDB(paths.knowledge_db()).meta()
    if not meta:
        _fail("知识库不存在,请先运行 cwagent knowledge sync")
    typer.echo(f"赛季 {meta['season_id']}-{meta['sub_season_id']},版本 {meta['game_version']},"
               f"同步于 {meta['synced_at']}")
    typer.echo("  " + "  ".join(f"{KIND_NAMES[k]} {v}" for k, v in meta["counts"].items()))
    for w in meta.get("warnings", []):
        typer.echo(f"  - {w}")


# ---------------------------------------------------------------- 截图采集与区域校对

COLLECT_HOTKEYS = ["ctrl+alt+s", "ctrl+alt+f11", "ctrl+shift+f11"]


@app.command("collect")
def collect(
    interval: Annotated[float, typer.Option(help="每隔多少秒自动截一张,0 表示只用热键")] = 2.0,
    seconds: Annotated[float, typer.Option(help="运行多少秒后自动停止,0 表示一直运行(Ctrl+C 停止)")] = 0,
    min_change: Annotated[float, typer.Option(help="画面变化小于该值(0~255 的平均像素差)时不保存")] = 2.0,
    focus: Annotated[bool, typer.Option(help="开始前把游戏窗口切到前台")] = False,
    hotkey: Annotated[bool, typer.Option(help="注册全局热键,按下时立即截一张(不去重)")] = True,
    keep_background: Annotated[bool, typer.Option(help="游戏不在前台时也保存")] = False,
) -> None:
    """手动游玩时采集游戏截图,保存到 data/fixtures/<日期>/。只能在 Windows 上运行。"""
    import sys
    import threading

    from . import paths
    from .replay.collector import Collector

    if sys.platform != "win32":
        _fail("collect 只能在 Windows 上运行")
    from .capture import windows as win

    source = win.GameWindowSource()
    try:
        info = source.window()
    except Exception as e:  # noqa: BLE001
        _fail(str(e))
    if not win.is_admin():
        typer.secho("提示:当前不是管理员权限。截图不受影响,但以后的键鼠操作需要管理员权限。", fg="yellow")
    if focus and not info.foreground:
        win.focus(info.hwnd)
    collector = Collector(source, paths.fixtures_dir(), min_change=min_change, keep_background=keep_background,
                          log=lambda m: typer.echo(time.strftime("[%H:%M:%S] ") + m))
    name = win.register_hotkey(COLLECT_HOTKEYS, lambda: collector.capture(force=True)) if hotkey else None
    typer.echo(f"游戏窗口 {info.width}x{info.height},保存到 {paths.fixtures_dir()}")
    typer.echo(("自动截图:每 %g 秒" % interval) if interval > 0 else "自动截图:关闭")
    typer.echo(f"热键截图:{name}" if name else "热键截图:未启用" + ("(候选热键都被占用)" if hotkey else ""))
    stop = threading.Event()
    try:
        stats = collector.run(interval=interval, seconds=seconds, stop=stop)
    except KeyboardInterrupt:
        stats = collector.stats
    finally:
        source.close()
    typer.echo(f"结束:保存 {stats.saved} 张,画面无变化跳过 {stats.skipped_same} 张,"
               f"不在前台跳过 {stats.skipped_background} 次,失败 {stats.errors} 次")


@regions_app.command("check")
def regions_check(
    image: Annotated[Path, typer.Argument(help="截图文件", exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="输出路径,默认 <截图名>.regions.png")] = None,
) -> None:
    """把 config/regions.yaml 里的区域框画到截图上(绿色已校准、橙色未校准),用于校对坐标。"""
    from PIL import Image

    from .vision import regions

    region_set = regions.load()
    with Image.open(image) as img:
        out = regions.draw(img, region_set)
        size = img.size
    target = output or image.with_name(image.stem + ".regions.png")
    out.save(target)
    ratio = size[0] / size[1]
    if abs(ratio - region_set.base[0] / region_set.base[1]) > 0.01:
        typer.secho(f"注意:截图比例 {size[0]}x{size[1]} 与基准 {region_set.base[0]}x{region_set.base[1]} 不同,"
                    "区域可能对不上", fg="yellow")
    calibrated = sum(r.calibrated for r in region_set.regions.values())
    typer.echo(f"已输出 {target}({len(region_set.regions)} 个区域,已校准 {calibrated} 个)")


# ---------------------------------------------------------------- 记忆

def _split(values: list[str] | None) -> list[str]:
    return [v.strip() for item in values or [] for v in item.replace("，", ",").split(",") if v.strip()]


@memory_app.command("list")
def memory_list(status: Annotated[str | None, typer.Option(help="只看某种状态:draft/approved/rejected/archived")] = None,
                full: Annotated[bool, typer.Option(help="显示完整内容")] = False) -> None:
    """列出全部记忆(公共 + 本机)。"""
    from .memory import MemoryStore

    items = MemoryStore().all()
    for m, where in items:
        if status and m.status != status:
            continue
        t = m.triggers
        trig = " ".join([*t.entities, *(f"节点:{n}" for n in t.nodes), *(f"界面:{s}" for s in t.screens)]) or "通用"
        s = m.stats
        typer.echo(f"{m.id}  [{m.label()}|{where}]  {m.title}")
        typer.echo(f"    触发: {trig}   注入 {s.injected} 次,有帮助 {s.helped},有害 {s.hurt}")
        if full:
            typer.echo("    " + m.content.strip().replace("\n", "\n    "))
    if not items:
        typer.echo("(还没有记忆)")


@memory_app.command("add")
def memory_add(
    kind: Annotated[str, typer.Option(help="机制 / 打法 / 教训")],
    title: Annotated[str, typer.Option(help="一句话标题")],
    content: Annotated[str, typer.Option(help="完整说明:条件、做法、原因")],
    entity: Annotated[list[str] | None, typer.Option(help="触发名字,可多次或用逗号分隔")] = None,
    node: Annotated[list[str] | None, typer.Option(help="触发节点类型,如 reward")] = None,
    screen: Annotated[list[str] | None, typer.Option(help="触发界面,如 prepare")] = None,
    scope: Annotated[list[str] | None, typer.Option(help="限定决策层:战略/战术/选择/事件")] = None,
    confidence: Annotated[str, typer.Option(help="high / medium / low")] = "medium",
    source: Annotated[str, typer.Option(help="来源")] = "维护者",
    approve: Annotated[bool, typer.Option(help="直接采纳(否则为待审核草稿)")] = True,
) -> None:
    """手动添加一条记忆(写入本机库)。"""
    from .memory import Memory, MemoryError, MemoryStore, Triggers

    try:
        memory = MemoryStore().add(Memory(
            kind=kind, title=title, content=content, confidence=confidence, source=source,
            triggers=Triggers(entities=_split(entity), nodes=_split(node), screens=_split(screen)),
            scope=_split(scope), status="approved" if approve else "draft"))
    except (MemoryError, ValueError) as e:
        _fail(str(e))
    typer.echo(f"已添加记忆 {memory.id}")


def _set_status(memory_id: str, status: str) -> None:
    from .memory import MemoryError, MemoryStore

    try:
        MemoryStore().set_status(memory_id, status)
    except MemoryError as e:
        _fail(str(e))
    typer.echo(f"{memory_id} → {status}")


@memory_app.command("approve")
def memory_approve(memory_id: str) -> None:
    """采纳一条记忆(之后会被注入决策)。"""
    _set_status(memory_id, "approved")


@memory_app.command("reject")
def memory_reject(memory_id: str) -> None:
    """驳回一条记忆。"""
    _set_status(memory_id, "rejected")


@memory_app.command("archive")
def memory_archive(memory_id: str) -> None:
    """归档一条不再使用的记忆。"""
    _set_status(memory_id, "archived")


@memory_app.command("export")
def memory_export(memory_id: str) -> None:
    """把一条记忆写进公共文件 knowledge/memories.yaml,之后可以提交到仓库。"""
    from .memory import MemoryError, MemoryStore

    try:
        path = MemoryStore().export(memory_id)
    except MemoryError as e:
        _fail(str(e))
    typer.echo(f"已写入 {path}")


@memory_app.command("preview")
def memory_preview(
    entity: Annotated[list[str] | None, typer.Option(help="局面里出现的名字,可多次或用逗号分隔")] = None,
    text: Annotated[list[str] | None, typer.Option(help="自由文本,如节点名、事件文字")] = None,
    node: Annotated[list[str] | None, typer.Option(help="当前 / 下一个节点类型")] = None,
    screen: Annotated[str | None, typer.Option(help="当前界面")] = None,
    layer: Annotated[str | None, typer.Option(help="决策层:战略/战术/选择/事件")] = None,
    draft: Annotated[bool, typer.Option(help="包含待审核的草稿")] = False,
) -> None:
    """给定一组线索,预览会被想起的记忆和注入提示词的文字。"""
    from .memory import Cues, MemoryStore, format_for_prompt, recall

    cues = Cues(entities=set(_split(entity)), texts=text or [], nodes=set(_split(node)), screen=screen, layer=layer)
    hits = recall([m for m, _ in MemoryStore().all()], cues, include_draft=draft)
    for r in hits:
        typer.echo(f"{r.score:5.2f}  {r.memory.id}  {r.memory.title}  ← {', '.join(r.reasons)}")
    typer.echo("\n" + (format_for_prompt(hits) or "(没有相关记忆)"))


# ---------------------------------------------------------------- 奇遇伙伴

@companions_app.command("list")
def companions_list() -> None:
    """列出已知的奇遇伙伴(公共档案 + 本机遇到的)。"""
    from .knowledge.companions import CompanionBook

    items = CompanionBook().all()
    if not items:
        typer.echo("(还没有记录任何奇遇伙伴)")
    for c in items:
        flags = [c.get("origin", ""), "已核实" if c.get("verified") else "未核实"]
        if c.get("seen_count"):
            flags.append(f"遇到 {c['seen_count']} 次")
        typer.echo(f"- {c['name']}  [{' | '.join(f for f in flags if f)}]  {c.get('type') or ''}")
        if c.get("desc"):
            typer.echo(f"    描述: {c['desc']}")
        for note in c.get("flow_effects") or []:
            typer.echo(f"    流程影响: {note}")


@companions_app.command("record")
def companions_record(
    name: Annotated[str, typer.Argument(help="伙伴名称")],
    desc: Annotated[str, typer.Option(help="游戏内的效果描述")] = "",
    flow: Annotated[list[str] | None, typer.Option(help="对流程的影响,可多次")] = None,
    screen: Annotated[list[str] | None, typer.Option(help="带来的专属界面名,可多次")] = None,
) -> None:
    """手动记一个奇遇伙伴到本机档案(对局中由 agent 自动记录)。"""
    from .knowledge.companions import CompanionBook

    try:
        entry, first = CompanionBook().record(name, desc=desc, flow_effects=flow, screens=screen)
    except ValueError as e:
        _fail(str(e))
    typer.echo(("新记录" if first else "已更新") + f":{entry['name']}(遇到 {entry['seen_count']} 次)")


@companions_app.command("export")
def companions_export(name: Annotated[str, typer.Argument(help="伙伴名称")]) -> None:
    """把一个伙伴写进公共档案 knowledge/companions.yaml,之后可以提交到仓库。"""
    from .knowledge.companions import CompanionBook

    try:
        path = CompanionBook().export(name)
    except KeyError:
        _fail(f"没有记录过 {name}")
    typer.echo(f"已写入 {path}")


# ---------------------------------------------------------------- 网页服务

@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="监听地址,默认取 config.yaml 的 server.host")] = None,
    port: Annotated[int | None, typer.Option(help="端口,默认取 config.yaml 的 server.port")] = None,
) -> None:
    """启动后端和网页界面。"""
    import uvicorn

    from .server import create_app
    from .stream import LiveStream

    cfg = ConfigStore().load()
    settings = cfg.server
    host = host or settings.host
    port = port or settings.port
    _ensure_web()
    if host not in ("127.0.0.1", "localhost"):
        typer.secho(f"注意:监听 {host},局域网内的其他设备可以访问并控制 agent(没有登录机制)", fg="yellow")
    typer.echo(f"打开 http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}")
    import threading

    from . import components

    # 外部组件(虚拟手柄驱动)缺了就在后台装上,不耽误网页启动
    threading.Thread(target=components.ensure_all, kwargs={"log": typer.echo}, daemon=True).start()
    live = LiveStream(cfg.stream, log=typer.echo)
    reason = live.start()
    if reason:
        typer.secho(f"实时画面不可用:{reason}", fg="yellow")
    try:
        uvicorn.run(create_app(live=live), host=host, port=port, log_level="warning")
    finally:
        live.stop()


@stream_app.command("setup")
def stream_setup(
    force: Annotated[bool, typer.Option("--force", help="已经下载过也重新下载")] = False,
) -> None:
    """下载 ffmpeg 和 MediaMTX 并放行防火墙(serve 启动时缺少也会自动下载,这个命令用于手动重装)。"""
    import sys

    if sys.platform != "win32":
        _fail("实时画面只能在 Windows 上使用")
    from .capture.windows import is_admin
    from .stream.live import ffmpeg_exe, pick_encoder
    from .stream.setup import FIREWALL_RULE, allow_firewall, install

    install(force=force, progress=typer.echo)
    cfg = ConfigStore().load().stream
    if is_admin():
        allow_firewall(cfg.udp_port)
        typer.echo(f"防火墙:已放行本地子网访问 UDP {cfg.udp_port}(规则名 {FIREWALL_RULE})")
    else:
        typer.secho("当前不是管理员,没有添加防火墙规则,局域网里的其他设备可能看不到画面。"
                    "以管理员身份重新运行本命令即可。", fg="yellow")
    typer.echo(f"编码器:{pick_encoder(ffmpeg_exe(), cfg.encoder)}。重启 cwagent serve 后生效。")


def _ensure_web() -> None:
    """没有网页界面时下载预构建包;有的话检查是不是和本地代码同一个提交构建的。"""
    from . import paths, webdist

    if not (paths.web_dist() / "index.html").exists():
        try:
            webdist.download(progress=typer.echo)
        except Exception as e:  # noqa: BLE001 - 下载不了只是没有网页,接口照常可用
            typer.secho(f"没有网页界面,只有接口可用({type(e).__name__}: {e})。"
                        "可以稍后运行 cwagent web download,或自己构建:cd web && npm install && npm run build", fg="yellow")
        return
    info, local = webdist.build_info(), webdist.local_commit()
    if info and local and info.get("commit") != local:
        typer.secho(f"提示:网页界面是提交 {str(info.get('commit'))[:7]} 构建的,本地代码是 {local[:7]};"
                    "界面不正常时运行 cwagent web download 更新", fg="yellow")


@app.command("uninstall")
def uninstall_cmd(
    everything: Annotated[bool, typer.Option("--all", help="同时删除知识库、对局记录、配置等全部数据")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="不再确认")] = False,
) -> None:
    """撤销 cwagent 对系统做的改动(虚拟手柄驱动、防火墙规则、下载的程序);--all 连数据一起删。"""
    from . import components

    items = components.plan(everything=everything)
    typer.echo("将要:\n" + "\n".join(f"- {i}" for i in items))
    if not everything:
        typer.echo("配置、对局记录、知识库保留;要一起删掉用 --all。")
    if not yes and not typer.confirm("继续?"):
        raise typer.Exit(1)
    components.uninstall(log=typer.echo, everything=everything)
    typer.echo("完成。" + ("现在可以删除程序所在的文件夹。" if everything else ""))


@web_app.command("download")
def web_download() -> None:
    """下载最新的网页界面预构建包(替换 web/dist)。"""
    from . import webdist

    try:
        webdist.download(progress=typer.echo)
    except Exception as e:  # noqa: BLE001
        _fail(f"下载失败:{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 对局

@app.command("play")
def play_cmd(
    model: Annotated[str | None, typer.Option(help="模型档案名,默认用备战环节的设置")] = None,
    max_steps: Annotated[int, typer.Option(help="最多执行多少步")] = 600,
) -> None:
    """在本机直接操作游戏,自动打一局(需在 Windows 上以管理员身份运行,游戏处于货币战争界面)。"""
    import sys

    if sys.platform != "win32":
        _fail("play 只能在 Windows 上运行")
    from .device.windows import WindowsDevice
    from .orchestrator.session import play

    device = WindowsDevice()
    try:
        play(ConfigStore().load(), device, model=model, max_steps=max_steps,
             log=lambda m: typer.echo(time.strftime("[%H:%M:%S] ") + m))
    finally:
        device.close()
