from __future__ import annotations

from pathlib import Path
from typing import TextIO

from agentos.cli.output import write_json_line


_PYPROJECT = """[project]
name = "agentos-app"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["agent-os"]
"""


def run_init(path: str, *, stdout: TextIO) -> None:
    """在指定目录创建最小 AgentOS 应用工程。"""

    if type(path) is not str or not path.strip():
        raise ValueError("path must not be empty")
    target = Path(path)
    project_file = target / "pyproject.toml"
    if project_file.exists():
        raise FileExistsError("project file already exists")
    target.mkdir(parents=True, exist_ok=True)
    with project_file.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_PYPROJECT)
    write_json_line({"initialized": True}, stream=stdout)


__all__ = ["run_init"]
