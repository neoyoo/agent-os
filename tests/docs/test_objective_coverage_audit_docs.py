from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _audit() -> str:
    text = (ROOT / "docs/agentos-objective-coverage-audit.md").read_text(
        encoding="utf-8",
    )
    return " ".join(text.split())


def test_audit_targets_current_release_and_records_goal_complete() -> None:
    text = _audit()

    assert "0.3.0a1" in text
    assert "Phase 6 distributed runtime cutover" in text
    assert "Goal status: complete" in text
    assert "| Release governance | complete |" in text
    assert "does not certify a deployment" in text


def test_audit_covers_current_sdk_objectives() -> None:
    text = _audit()

    for expected in (
        "One execution kernel",
        "Local agent",
        "Durable agent",
        "Distributed Run lifecycle",
        "Distributed artifacts",
        "Delivery and replay",
        "HTTP/SSE/WebSocket",
        "A2A wire/channel support",
        "Team coordination",
        "Planner pattern layer",
        "Context protocol",
        "Side effects",
        "Live failure evidence",
        "Release governance",
    ):
        assert expected in text


def test_audit_records_architecture_invariants() -> None:
    text = _audit()

    for expected in (
        "PostgreSQL is the sole distributed state truth",
        "Redis is delivery, lease, and bounded replay infrastructure only",
        "Artifact bytes are shared through `BlobStore`",
        "Transports do not own execution",
        "version plus claim/fencing guards",
        "commits authoritative state before ACK",
        "Waiting exits the active loop",
        "No synchronous distributed store wrapper",
    ):
        assert expected in text


def test_audit_records_breaking_removals_without_compatibility_claims() -> None:
    text = _audit()

    for expected in (
        "production reference web example",
        "legacy distributed Session Snapshot persistence",
        "synchronous PostgreSQL/Redis wrappers",
        "superseded channel and multi-agent modules",
        "phase6-distributed-runtime-breaking-map.md",
    ):
        assert expected in text


def test_audit_records_explicit_deferrals_and_completion_gate() -> None:
    text = _audit()

    for expected in (
        "".join(("O", "C", "R")),
        "automatic attachment summaries",
        "attachment embeddings and vector retrieval",
        "Provider transcript recovery",
        "global exactly-once execution",
        "cross-region multi-primary state",
        "architecture tests",
        "live integration tests",
        "independent spec-compliance",
        "code-quality/security reviews",
        "no open P0, P1, or P2 findings",
    ):
        assert expected in text
