"""注册 cwagent HTTP MCP 并安装 Codex 游玩 skill。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8770/mcp?wait=45")
    parser.add_argument("--skills-dir", type=Path)
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme not in ("http", "https") or not url.hostname or not url.path.endswith("/mcp"):
        parser.error("--url 必须是 cwagent 的 HTTP MCP 地址,路径以 /mcp 结尾")
    codex = shutil.which("codex")
    if codex is None:
        parser.error("没有找到 codex,请先安装 Codex CLI 并加入 PATH")
    subprocess.run([codex, "mcp", "add", "cwagent", "--url", args.url], check=True)
    source = Path(__file__).parent / "skills" / "cwagent-play" / "SKILL.md"
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    target = (args.skills_dir or home / "skills") / "cwagent-play" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"已注册 cwagent MCP:{args.url}\n已安装 skill:{target}")


if __name__ == "__main__":
    main()
