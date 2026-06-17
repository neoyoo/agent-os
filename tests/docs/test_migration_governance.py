from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_all_sql_migrations_define_up_and_down_sections() -> None:
    migration_paths = sorted((ROOT / "docs" / "migrations").glob("*.sql"))

    assert migration_paths
    for migration_path in migration_paths:
        migration = migration_path.read_text(encoding="utf-8")
        up_index = migration.find("-- migrate:up")
        down_index = migration.find("-- migrate:down")

        assert up_index != -1, f"{migration_path.name} is missing -- migrate:up"
        assert down_index != -1, f"{migration_path.name} is missing -- migrate:down"
        assert (
            up_index < down_index
        ), f"{migration_path.name} must define -- migrate:up before -- migrate:down"
