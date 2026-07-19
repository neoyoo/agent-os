from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (
    "SessionSnapshot",
    "PostgresSessionSnapshotPersistence",
    "RedisHotSessionStore",
)


def test_distributed_runtime_does_not_import_legacy_snapshot_path() -> None:
    source_root = PROJECT_ROOT / "src" / "agentos" / "distributed"
    assert source_root.is_dir()

    violations = {
        str(path.relative_to(PROJECT_ROOT)): token
        for path in source_root.rglob("*.py")
        for token in FORBIDDEN
        if token in path.read_text(encoding="utf-8")
    }

    assert violations == {}
