"""将 cwagent 适配安装为 Antigravity 全局插件(MCP + skill + 续行 hook)。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_URL = "http://127.0.0.1:8770/mcp"


def normalize_url(raw: str, wait: int | None = None) -> str:
    """校验并规范化 cwagent MCP 地址;省略 /mcp 路径时自动补齐。"""
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("--url 必须是 http:// 或 https:// 开头的 cwagent 地址")
    path = parts.path or ""
    if path in ("", "/"):
        path = "/mcp"
    elif not path.endswith("/mcp"):
        raise ValueError("--url 路径必须是根目录或以 /mcp 结尾")

    query_items = list(parse_qsl(parts.query, keep_blank_values=False))
    if wait is not None:
        if not 1 <= wait <= 3600:
            raise ValueError("--wait 必须在 1 到 3600 秒之间")
        query_items = [(k, v) for k, v in query_items if k != "wait"]
        query_items.append(("wait", str(wait)))
    query = urlencode(query_items)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _hook_command(python_exe: str, event: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([python_exe, "scripts/hook.py", event])
    return f"{shlex.quote(python_exe)} scripts/hook.py {event}"


def default_target() -> Path:
    base = Path(os.environ.get("ANTIGRAVITY_CONFIG_HOME", str(Path.home() / ".gemini" / "config")))
    return base / "plugins" / "cwagent"


def install(
    *,
    url: str,
    target: Path,
    python_exe: str,
    with_hooks: bool = True,
    source_dir: Path | None = None,
) -> Path:
    src = source_dir or Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)

    for rel in ("plugin.json", "README.md"):
        shutil.copy2(src / rel, target / rel)

    skill_dst = target / "skills" / "cwagent-play" / "SKILL.md"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "skills" / "cwagent-play" / "SKILL.md", skill_dst)

    mcp_config = {"mcpServers": {"cwagent": {"url": url}}}
    (target / "mcp_config.json").write_text(
        json.dumps(mcp_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    hooks_dst = target / "hooks.json"
    scripts_dst = target / "scripts" / "hook.py"
    if with_hooks:
        scripts_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "scripts" / "hook.py", scripts_dst)
        hooks_cfg = json.loads((src / "hooks.json").read_text(encoding="utf-8"))
        spec = hooks_cfg["cwagent-keep-playing"]
        spec["PostToolUse"][0]["hooks"][0]["command"] = _hook_command(python_exe, "post-tool")
        spec["Stop"][0]["command"] = _hook_command(python_exe, "stop")
        hooks_dst.write_text(
            json.dumps(hooks_cfg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        hooks_dst.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"cwagent serve 或 /mcp 地址(默认 {DEFAULT_URL})")
    parser.add_argument("--wait", type=int, help="覆盖 MCP 长等待秒数(1-3600;默认不传,使用服务端默认的 50 秒)")
    parser.add_argument("--target", type=Path, help="插件安装目录(默认 ~/.gemini/config/plugins/cwagent)")
    parser.add_argument("--python", default=sys.executable, help="hooks.json 使用的 Python 解释器路径(默认当前解释器)")
    parser.add_argument("--no-hooks", action="store_true", help="不安装自动续行 hooks.json,只安装 MCP 和 skill")
    args = parser.parse_args(argv)

    try:
        mcp_url = normalize_url(args.url, wait=args.wait)
    except ValueError as e:
        parser.error(str(e))

    target = (args.target or default_target()).expanduser().resolve()
    install(url=mcp_url, target=target, python_exe=args.python, with_hooks=not args.no_hooks)

    lines = [
        f"已安装 Antigravity 插件到:{target}",
        f"MCP 地址:{mcp_url}",
        f"自动续行 hook:{'已启用' if not args.no_hooks else '未安装(--no-hooks)'}",
        "请重启 Antigravity 使新插件目录生效;重启后可在 Customizations / Plugins 中启停。",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
