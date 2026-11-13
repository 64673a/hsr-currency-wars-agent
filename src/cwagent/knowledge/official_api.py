"""米游社「货币战争专题页」背后的官方接口。设计见 docs/TECH_DESIGN.md 3.1。

接口是网页自身调用的非官方公开接口:只做手动触发的同步,请求间隔不低于 1 秒。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

BASE = "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game"
CONFIG_URL = BASE + "/config"
LINEUP_URL = BASE + "/lineup/index"

# config 接口必须存在的字段;缺失说明接口结构变了,应报错而不是静默入库
REQUIRED_CONFIG_KEYS = (
    "role_list", "equipment_list", "fight_augment_list", "portal_list", "trait_info_list",
    "role_property_list", "spot_light_list", "season_id", "rpg_game_big_version",
)


class OfficialApiError(RuntimeError):
    pass


class OfficialApi:
    def __init__(self, *, interval: float = 1.0, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = max(1.0, interval)
        self._sleep = sleep
        self._last = 0.0
        self._own = http is None
        self.http = http or httpx.Client(timeout=30.0, headers={
            "x-rpc-platform": "pc",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-device_id": str(uuid.uuid4()),
            "referer": "https://act.miyoushe.com/",
            "origin": "https://act.miyoushe.com",
            "user-agent": "Mozilla/5.0 (cwagent knowledge sync)",
        })

    def close(self) -> None:
        if self._own:
            self.http.close()

    def __enter__(self) -> "OfficialApi":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self._last + self.interval - time.monotonic()
        if self._last and wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def _call(self, method: str, url: str, **kw) -> dict[str, Any]:
        self._throttle()
        try:
            resp = self.http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise OfficialApiError(f"请求 {url} 失败:{type(e).__name__}: {e}") from None
        if resp.status_code != 200:
            raise OfficialApiError(f"请求 {url} 返回 HTTP {resp.status_code}:{resp.text[:200]}")
        try:
            body = resp.json()
        except ValueError:
            raise OfficialApiError(f"{url} 的响应不是 JSON") from None
        if body.get("retcode") != 0:
            raise OfficialApiError(f"{url} 返回 retcode={body.get('retcode')}:{body.get('message')}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise OfficialApiError(f"{url} 的 data 为空(请求头可能已失效)")
        return data

    def config(self) -> dict[str, Any]:
        data = self._call("GET", CONFIG_URL, params={"game": "hkrpg"})
        missing = [k for k in REQUIRED_CONFIG_KEYS if k not in data]
        if missing:
            raise OfficialApiError(f"config 接口结构变了,缺少字段:{', '.join(missing)}")
        return data

    def lineups(self, *, max_count: int, on_page: Callable[[int], None] | None = None) -> list[dict[str, Any]]:
        """按热度翻页抓取阵容,直到达到 max_count、没有下一页或整页都已过期。"""
        out: list[dict[str, Any]] = []
        token = ""
        page = 1
        while len(out) < max_count:
            data = self._call("POST", LINEUP_URL, json={
                "game": "hkrpg", "page": str(page), "limit": "10", "lineup_type": "Tourn",
                "next_page_token": token, "role_ids": [], "trait_ids": [], "match_change_job": False,
                "match_hard": False, "order": "Hot",
            })
            items = data.get("list") or []
            if not isinstance(items, list):
                raise OfficialApiError("阵容接口结构变了:list 不是数组")
            out.extend(items)
            if on_page:
                on_page(len(out))
            token = data.get("next_page_token") or ""
            if not items or not token:
                break
            if all((i.get("tourn_detail") or {}).get("is_expired") for i in items):
                break
            page += 1
        return out[:max_count]
