from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from agentos.cli.commands.init import run_init


def test_init_writes_minimal_project_without_runtime_toml(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    stdout = StringIO()

    run_init(str(target), stdout=stdout)

    assert (target / "pyproject.toml").read_text(encoding="utf-8") == (
        '[project]\nname = "agentos-app"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["agent-os"]\n'
    )
    assert not (target / "agentos.toml").exists()
    assert stdout.getvalue() == '{"initialized":true}\n'


def test_init_refuses_to_overwrite_existing_project_file(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    project = target / "pyproject.toml"
    project.write_text("owned", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_init(str(target), stdout=StringIO())

    assert project.read_text(encoding="utf-8") == "owned"
