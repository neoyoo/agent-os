from __future__ import annotations

from importlib import import_module
from importlib.resources import files
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


def test_cli_migrate_uses_packaged_postgres_migrations() -> None:
    cli_main = import_module("agentos.cli.main")
    package_names = sorted(
        item.name
        for item in files("agentos.migrations").iterdir()
        if item.name.endswith(".sql")
    )

    assert [path.name for path in cli_main._migration_paths()] == package_names
    assert all("sqlite" not in path.name for path in cli_main._migration_paths())


def test_packaged_postgres_migrations_match_docs_sources() -> None:
    package_files = {
        item.name: item.read_text()
        for item in files("agentos.migrations").iterdir()
        if item.name.endswith(".sql")
    }

    for name, package_sql in package_files.items():
        assert package_sql == Path("docs/migrations", name).read_text()
