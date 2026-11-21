"""注册 cwagent HTTP MCP 并安装 Codex 游玩 skill。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qsl, urlencode, urlparse


TOOL_TIMEOUT = 1920  # 秒,比地址里的 wait 长:战斗期间 wait / act 一直等到下一个局面,不回来调用模型
AGENT = "codex"      # 地址里的 agent 参数:网页上按它给这个 agent 填模型和思考强度


def set_tool_timeout(config: Path, name: str, seconds: int) -> None:
    """`codex mcp add` 设不了工具超时(默认 60 秒),在 config.toml 的 [mcp_servers.<name>] 里补上 tool_timeout_sec。"""
    text = config.read_text(encoding="utf-8")
    header = re.search(rf"^\[mcp_servers\.{re.escape(name)}\]\s*$", text, flags=re.M)
    if header is None:
        raise SystemExit(f"{config} 里没有 [mcp_servers.{name}]")
    end = re.search(r"^\[", text[header.end():], flags=re.M)
    stop = header.end() + (end.start() if end else len(text) - header.end())
    table = re.sub(r"^tool_timeout_sec\s*=.*\n?", "", text[header.end():stop], flags=re.M)
    table = f"\ntool_timeout_sec = {seconds}" + table if table.startswith("\n") else f"\ntool_timeout_sec = {seconds}\n" + table
    config.write_text(text[:header.end()] + table + text[stop:], encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=f"http://127.0.0.1:8770/mcp?wait=1800&agent={AGENT}")
    parser.add_argument("--skills-dir", type=Path)
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme not in ("http", "https") or not url.hostname or not url.path.endswith("/mcp"):
        parser.error("--url 必须是 cwagent 的 HTTP MCP 地址,路径以 /mcp 结尾")
    query = parse_qsl(url.query)
    if not any(k == "agent" for k, _ in query):
        args.url = url._replace(query=urlencode([*query, ("agent", AGENT)])).geturl()
    codex = shutil.which("codex")
    if codex is None:
        parser.error("没有找到 codex,请先安装 Codex CLI 并加入 PATH")
    subprocess.run([codex, "mcp", "add", "cwagent", "--url", args.url], check=True)
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    set_tool_timeout(home / "config.toml", "cwagent", TOOL_TIMEOUT)
    source = Path(__file__).parent / "skills" / "cwagent-play" / "SKILL.md"
    target = (args.skills_dir or home / "skills") / "cwagent-play" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"已注册 cwagent MCP:{args.url}(工具超时 {TOOL_TIMEOUT} 秒)\n已安装 skill:{target}")


if __name__ == "__main__":
    main()
