from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")
    return " ".join(text.split())


def test_readiness_defines_three_runtime_levels_and_optional_dependencies() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "LocalRuntimeProfile",
        "DurableRuntimeProfile",
        "DistributedRuntimeProfile",
        "agentos[durable]",
        "agentos[distributed]",
        "Local and Durable imports must not load PostgreSQL or Redis clients",
    ):
        assert expected in text


def test_readiness_freezes_distributed_truth_and_ack_order() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "PostgreSQL is the only authoritative distributed state source",
        "PostgresStateStore",
        "PostgresArtifactStore",
        "RedisQueueAdapter",
        "RedisEventReplayAdapter",
        "BlobStore",
        "DistributedWorker",
        "PostgreSQL commit",
        "Redis ACK",
        "must not ACK",
    ):
        assert expected in text


def test_readiness_keeps_channels_and_transports_stateless() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "ChannelServices",
        "DistributedAsgiApp",
        "HTTP, SSE, WebSocket, and A2A",
        "Transports parse and serialize wire data only",
        "fail closed by default",
        "tenant-scoped `RequestScope`",
        "Cross-tenant lookups",
    ):
        assert expected in text


def test_readiness_defines_context_and_artifact_projection() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "Every Provider attempt rebuilds context",
        "StoredMessage contains an Artifact handle",
        "not raw image or file bytes",
        "mounts the media part",
        "not part of the UI conversation record",
    ):
        assert expected in text


def test_readiness_defines_side_effect_cancel_safe_stop() -> None:
    text = _read("docs/production-readiness.md")
    migration = _read("docs/migrations/phase6-distributed-runtime-breaking-map.md")

    for document in (text, migration):
        for expected in (
            "409",
            "side_effect_in_flight",
            "resolve_side_effect",
            "command_id",
            "FAILED",
        ):
            assert expected in document

    assert "If the Run remains non-terminal" in text
    assert "do not submit another cancel" in text


def test_readiness_requires_canonical_live_backend_evidence() -> None:
    text = _read("docs/production-readiness.md")

    for backend in (
        "postgres_state_store",
        "postgres_artifact_store",
        "redis_worker_queue",
        "redis_relay_queue",
        "redis_event_replay",
        "distributed_worker",
    ):
        assert backend in text

    for expected in (
        "DeploymentLiveBackendVerificationProfile",
        "ReferenceLiveBackendProbePack",
        "non-certifying",
        "does not create backend clients",
        "ProductionReadinessEvidenceBundle",
    ):
        assert expected in text


def test_readiness_names_failure_deadlines_and_deployment_ownership() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "bounded I/O deadline",
        "CancelledError",
        "heartbeat_interval + heartbeat_cycle_timeout",
        "Outbox relay",
        "Worker drain",
        "shared BlobStore credentials",
        "migration execution and rollback",
        "physical sandboxing",
    ):
        assert expected in text


def test_readiness_excludes_deferred_attachment_and_global_semantics() -> None:
    text = _read("docs/production-readiness.md")

    for expected in (
        "".join(("O", "C", "R")),
        "attachment embedding/vector retrieval",
        "automatic attachment summaries",
        "Provider transcript recovery",
        "global exactly-once execution",
        "cross-region multi-primary state",
    ):
        assert expected in text


def test_readiness_lists_release_gate_commands() -> None:
    text = _read("docs/production-readiness.md")

    for command in (
        "uv run pytest tests/architecture -q",
        "uv run pytest -q",
        "uv run pytest -m integration -q",
        "uv run ruff check src tests",
        "uv run python -m compileall -q src tests",
        "git diff --check",
    ):
        assert command in text
