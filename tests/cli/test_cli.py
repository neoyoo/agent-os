from __future__ import annotations

from pathlib import Path

from agentos.cli import main


def test_cli_init_writes_project_skeleton(tmp_path: Path) -> None:
    exit_code = main(["init", str(tmp_path / "demo")])

    assert exit_code == 0
    assert (tmp_path / "demo" / "pyproject.toml").exists()
    assert (tmp_path / "demo" / "agentos.toml").exists()


def test_cli_migrate_prints_migration_paths(capsys) -> None:
    exit_code = main(["migrate", "--dry-run"])

    assert exit_code == 0
    assert "postgres-multi-agent-tasks" in capsys.readouterr().out


def test_cli_migrate_finds_migrations_outside_project_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main(["migrate", "--dry-run"])

    assert exit_code == 0
    assert "postgres-multi-agent-tasks" in capsys.readouterr().out
