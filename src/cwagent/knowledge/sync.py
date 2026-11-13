"""知识同步:拉取官方数据 → 整理 → 写入 sqlite → 导出 YAML → 下载模板图标。"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
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


@dataclass
class SyncResult:
    counts: dict[str, int]
    meta: dict[str, str]
    warnings: list[str]
    lineups_fetched: int
    templates: dict[str, int] = field(default_factory=dict)  # 类别 -> 新下载数
    template_errors: list[str] = field(default_factory=list)


def load_companions(path: Path | None = None) -> list[dict]:
    path = path or paths.companions_file()
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else data.get("companions", [])


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
    knowledge = build(config, lineups, load_companions())
    report("write", "写入知识库")
    db.write(db_path or paths.knowledge_db(), knowledge, {"config": config, "lineups": lineups})
    export_yaml(knowledge, export_dir or paths.export_dir())

    result = SyncResult(counts=knowledge.counts(), meta=knowledge.meta, warnings=knowledge.warnings,
                        lineups_fetched=len(lineups))
    if templates:
        report("templates", "下载模板图标")
        result.templates, result.template_errors = download_templates(
            knowledge.icons, templates_dir or paths.templates_dir(), progress=report)
    report("done", json.dumps(result.counts, ensure_ascii=False))
    return result
