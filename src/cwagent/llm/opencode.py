"""OpenCode Go 的会话和客户端标识。"""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import uuid4

from .. import __version__


def request_headers(base_url: str, headers: dict[str, str], session_id: str | None = None,
                    *, enabled: bool | None = None) -> dict[str, str]:
    if enabled is None:
        url = urlsplit(base_url)
        enabled = url.hostname == "opencode.ai" and any(
            url.path == prefix or url.path.startswith(prefix + "/")
            for prefix in ("/zen/go", "/inference/go")
        )
    if not enabled:
        return headers
    # 会话由对局管理,不沿用配置里写死的值;请求头名称不区分大小写。
    out = {k: v for k, v in headers.items() if k.lower() != "x-opencode-session"}
    out["x-opencode-session"] = session_id or str(uuid4())
    if not any(k.lower() == "user-agent" for k in out):
        out["User-Agent"] = f"cwagent/{__version__}"
    return out
