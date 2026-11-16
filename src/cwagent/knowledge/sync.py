"""知识同步:拉取官方数据 → 整理 → 写入 sqlite → 导出 YAML → 下载模板图标。"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from .. import paths
from . import db
from .build import Knowledge, build
from .official_api import OfficialApi

Progress = Callable[[str, str], None]  # (阶段, 说明)

# March7thAssistant(GPL-3.0)仓库里的角色头像:和货币战争的卡面是同一套立绘,还包括皮肤。
# 名字表是「文件名 -> 中文名」,皮肤写成「火花·甜梦电波」「流萤「春日手信」」。
M7A_RAW = "https://raw.githubusercontent.com/moesnow/March7thAssistant/main"
M7A_NAMES = M7A_RAW + "/assets/config/character_names.json"
M7A_AVATAR = M7A_RAW + "/assets/images/share/character/{}.png"


@dataclass
class SyncResult:
    counts: dict[str, int]
    meta: dict[str, str]
    warnings: list[str]
    lineups_fetched: int
    templates: dict[str, int] = field(default_factory=dict)  # 类别 -> 新下载数
    template_errors: list[str] = field(default_factory=list)


def load_companions() -> list[dict]:
    """公共档案 + 本机遇到的奇遇伙伴。"""
    from .companions import CompanionBook

    return CompanionBook().all()


def load_affixes() -> list[dict]:
    """公共档案 + 本机读到的敌人词条。"""
    from .affixes import AffixBook

    return AffixBook().list()


def export_yaml(knowledge: Knowledge, out_dir: Path) -> None:
    """每类条目导出一个 YAML 文件,便于人工查看和比较版本差异。"""
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for kind, entries in knowledge.entries.items():
        items = [{k: v for k, v in e.data.items() if k not in ("icon", "big_icon")} for e in entries]
        (tmp / f"{kind}.yaml").write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120),
                                          encoding="utf-8")
    (tmp / "meta.yaml").write_text(yaml.safe_dump({**knowledge.meta, "counts": knowledge.counts(),
                                                   "warnings": knowledge.warnings}, allow_unicode=True),
                                   encoding="utf-8")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(tmp, out_dir)


def download_templates(icons: dict[str, dict[str, str]], out_dir: Path, *, workers: int = 4,
                       progress: Progress | None = None,
                       http: httpx.Client | None = None) -> tuple[dict[str, int], list[str]]:
    """下载模板图标到 <out_dir>/<类别>/<id>.<扩展名>,已存在的跳过。返回各类新下载数和错误列表。"""
    jobs = []
    for kind, mapping in icons.items():
        (out_dir / kind).mkdir(parents=True, exist_ok=True)
        for key, url in mapping.items():
            ext = Path(urlparse(url).path).suffix or ".png"
            target = out_dir / kind / f"{key}{ext}"
            if not (target.exists() and target.stat().st_size > 0):
                jobs.append((kind, url, target))
    downloaded: dict[str, int] = {k: 0 for k in icons}
    errors: list[str] = []
    if not jobs:
        return downloaded, errors
    own = http is None
    client = http or httpx.Client(timeout=30.0, follow_redirects=True)

    def fetch(job):
        kind, url, target = job
        resp = client.get(url)
        resp.raise_for_status()
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(resp.content)
        os.replace(tmp, target)
        return kind

    try:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch, job): job for job in jobs}
            for i, fut in enumerate(cf.as_completed(futures), 1):
                kind, url, _ = futures[fut]
                try:
                    downloaded[fut.result()] += 1
                except Exception as e:  # noqa: BLE001 - 单个图标失败不影响整体
                    errors.append(f"{kind} {url}: {e}")
                if progress and (i % 50 == 0 or i == len(jobs)):
                    progress("templates", f"图标 {i}/{len(jobs)}")
    finally:
        if own:
            client.close()
    return downloaded, errors


def download_avatars(out_dir: Path, *, progress: Progress | None = None,
                     http: httpx.Client | None = None) -> tuple[int, list[str]]:
    """下载 M7A 的角色头像和名字表到 <out_dir>/avatars/。名字表每次都更新,头像已存在的跳过。"""
    own = http is None
    client = http or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        try:
            resp = client.get(M7A_NAMES)
            resp.raise_for_status()
            names = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            return 0, [f"avatars {M7A_NAMES}: {e}"]
        folder = out_dir / "avatars"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "names.json.part"
        tmp.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, folder / "names.json")
        done, errors = download_templates({"avatars": {k: M7A_AVATAR.format(k) for k in names}}, out_dir,
                                          progress=progress, http=client)
        return done["avatars"], errors
    finally:
        if own:
            client.close()


def rebuild(*, db_path: Path | None = None, export_dir: Path | None = None) -> SyncResult:
    """不联网,用库里存的上次接口原始响应(raw_snapshots)重新整理一遍知识库。改了整理逻辑(如攻略卡片多存了字段)
    之后用,不必再请求官方接口;奇遇伙伴、敌人词条照常从 YAML 读最新的。"""
    path = db_path or paths.knowledge_db()
    if not path.exists():
        raise FileNotFoundError(f"没有知识库:{path},先运行 cwagent knowledge sync")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        raw = {name: json.loads(text) for name, text in con.execute("SELECT name, json FROM raw_snapshots")}
    finally:
        con.close()
    if "config" not in raw or "lineups" not in raw:
        raise FileNotFoundError("知识库里没有上次同步的原始响应,先运行 cwagent knowledge sync")
    knowledge = build(raw["config"], raw["lineups"], load_companions(), load_affixes())
    db.write(path, knowledge, {"config": raw["config"], "lineups": raw["lineups"]})
    export_yaml(knowledge, export_dir or paths.export_dir())
    return SyncResult(counts=knowledge.counts(), meta=knowledge.meta, warnings=knowledge.warnings,
                      lineups_fetched=len(raw["lineups"]))


def sync(*, max_lineups: int = 300, interval: float = 1.0, templates: bool = True,
         progress: Progress | None = None, api: OfficialApi | None = None,
         db_path: Path | None = None, export_dir: Path | None = None,
         templates_dir: Path | None = None) -> SyncResult:
    report = progress or (lambda stage, msg: None)
    own = api is None
    api = api or OfficialApi(interval=interval)
    try:
        report("config", "拉取静态配置")
        config = api.config()
        report("lineups", "拉取官方阵容")
        lineups = api.lineups(max_count=max_lineups, on_page=lambda n: report("lineups", f"阵容 {n} 条"))
    finally:
        if own:
            api.close()

    report("build", "整理数据")
    knowledge = build(config, lineups, load_companions(), load_affixes())
    report("write", "写入知识库")
    db.write(db_path or paths.knowledge_db(), knowledge, {"config": config, "lineups": lineups})
    export_yaml(knowledge, export_dir or paths.export_dir())

    result = SyncResult(counts=knowledge.counts(), meta=knowledge.meta, warnings=knowledge.warnings,
                        lineups_fetched=len(lineups))
    if templates:
        report("templates", "下载模板图标")
        out = templates_dir or paths.templates_dir()
        result.templates, result.template_errors = download_templates(knowledge.icons, out, progress=report)
        report("templates", "下载 M7A 角色头像")
        result.templates["avatars"], errors = download_avatars(out, progress=report)
        result.template_errors += errors
    report("done", json.dumps(result.counts, ensure_ascii=False))
    return result
