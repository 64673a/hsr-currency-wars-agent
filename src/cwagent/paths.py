"""项目内各类文件的位置。

项目根目录按以下顺序确定:环境变量 CWAGENT_HOME;本包以可编辑方式安装时(uv sync 的默认行为)
包所在仓库的根目录;当前工作目录。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def root() -> Path:
    env = os.environ.get("CWAGENT_HOME")
    if env:
        return Path(env).resolve()
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo
    return Path.cwd().resolve()


def config_file() -> Path:
    return root() / "config.yaml"


def data_dir() -> Path:
    return root() / "data"


def knowledge_db() -> Path:
    return data_dir() / "knowledge.sqlite"


def templates_dir() -> Path:
    return data_dir() / "templates"


def export_dir() -> Path:
    return data_dir() / "export"


def fixtures_dir() -> Path:
    return data_dir() / "fixtures"


def regions_file() -> Path:
    return root() / "config" / "regions.yaml"


def companions_file() -> Path:
    return root() / "knowledge" / "companions.yaml"


def web_dist() -> Path:
    return root() / "web" / "dist"


def runs_dir() -> Path:
    return root() / "runs"


def tools_dir() -> Path:
    """下载的外部程序(ffmpeg、MediaMTX)。"""
    return data_dir() / "tools"


def stream_dir() -> Path:
    """实时画面服务运行时生成的配置和日志。"""
    return data_dir() / "stream"
