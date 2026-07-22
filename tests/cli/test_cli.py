from __future__ import annotations

from importlib import import_module
from importlib.resources import files
from pathlib import Path

from agentos.cli import main


def test_cli_init_writes_project_skeleton(tmp_path: Path) -> None:
    exit_code = main(["init", str(tmp_path / "demo")])

    assert exit_code == 0
    assert (tmp_path / "demo" / "pyproject.toml").exists()
    assert not (tmp_path / "demo" / "agentos.toml").exists()


def test_cli_rejects_legacy_migration_flags(capsys) -> None:
    for legacy in ("--dry-run", "--dsn"):
        assert main(["migrate", legacy]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            '{"code":"invalid_cli_input","message":"invalid CLI input"}\n'
        )


def test_cli_main_has_no_legacy_migration_glob() -> None:
    cli_main = import_module("agentos.cli.main")

    assert not hasattr(cli_main, "_migration_paths")


def test_packaged_postgres_migrations_match_docs_sources() -> None:
    package_files = {
        item.name: item.read_text()
        for item in files("agentos.migrations").iterdir()
        if item.name.endswith(".sql")
    }

    for name, package_sql in package_files.items():
        assert package_sql == Path("docs/migrations", name).read_text()


def test_all_documented_postgres_migrations_are_packaged() -> None:
    docs_postgres_names = sorted(
        path.name for path in Path("docs/migrations").glob("*postgres*.sql")
    )
    package_names = sorted(
        item.name
        for item in files("agentos.migrations").iterdir()
        if item.name.endswith(".sql")
    )

    assert package_names == docs_postgres_names
