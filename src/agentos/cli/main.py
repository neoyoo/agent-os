from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    """agent-os CLI 入口。"""

    parser = argparse.ArgumentParser(prog="agent-os")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_parser = subcommands.add_parser("init")
    init_parser.add_argument("path")

    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("app", nargs="?", default="agentos_app:app")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8000)

    migrate_parser = subcommands.add_parser("migrate")
    migrate_parser.add_argument("--dsn")
    migrate_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "init":
        return _init_project(Path(args.path))
    if args.command == "run":
        return _run_asgi(args.app, host=args.host, port=args.port)
    if args.command == "migrate":
        return _migrate(dsn=args.dsn, dry_run=args.dry_run)
    return 2


def _init_project(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "agentos-app"',
                'version = "0.1.0"',
                'requires-python = ">=3.11"',
                'dependencies = ["agent-os"]',
                "",
            ],
        ),
    )
    (path / "agentos.toml").write_text(
        "\n".join(
            [
                "[agent]",
                'provider = "openai-compatible"',
                'model = "gpt-4.1-mini"',
                "",
            ],
        ),
    )
    return 0


def _run_asgi(app: str, *, host: str, port: int) -> int:
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError("agent-os run requires installing uvicorn") from error
    uvicorn.run(app, host=host, port=port)
    return 0


def _migrate(*, dsn: str | None, dry_run: bool) -> int:
    migrations = sorted(Path("docs/migrations").glob("*.sql"))
    if dry_run or not dsn:
        for migration in migrations:
            print(migration)
        return 0
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError("agent-os migrate requires agentos[postgres]") from error
    with psycopg.connect(dsn) as connection:
        for migration in migrations:
            sql = migration.read_text()
            up_sql = sql.split("-- migrate:down", 1)[0].replace("-- migrate:up", "")
            connection.execute(up_sql)
        connection.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
