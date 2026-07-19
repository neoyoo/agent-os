from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_durable_store_does_not_hold_runtime_checkpoint_sources() -> None:
    durable_port = (
        ROOT / "src" / "agentos" / "runtime" / "durable_runtime.py"
    ).read_text(encoding="utf-8")
    sqlite_store = (
        ROOT / "src" / "agentos" / "durable" / "sqlite_store.py"
    ).read_text(encoding="utf-8")

    assert "bind_checkpoint_source" not in durable_port
    assert "bind_checkpoint_source" not in sqlite_store
    assert "RuntimeCheckpointSource" not in sqlite_store
    assert "_sources" not in sqlite_store
