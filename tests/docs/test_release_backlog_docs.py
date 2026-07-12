from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def assert_phrase(text: str, expected: str) -> None:
    normalized = " ".join(text.split())
    assert expected in text or expected in normalized


def test_release_backlog_classifies_remaining_p2_items() -> None:
    backlog = (ROOT / "docs" / "release-backlog.md").read_text(encoding="utf-8")
    production_readiness = (ROOT / "docs" / "production-readiness.md").read_text(
        encoding="utf-8",
    )
    skill = (ROOT / ".claude" / "skills" / "agent-os" / "SKILL.md").read_text(
        encoding="utf-8",
    )

    for expected in [
        "RC P2 Release Backlog",
        "planner dispatch crash window",
        "plan mutation and coordinator spawn",
        "dispatch outbox",
        "pending-dispatch marker",
        "compensation scanner",
        "non-blocking for RC",
        "PlanStore public boundary tests",
        "focused planner behavior tests",
        "no known P1 behavior bug",
        "A2A public operation rate limiting",
        "PeerKeyA2AOperationRateLimitPolicy",
        "A2AOperationServer(rate_limit_policy=...)",
        "distributed/global quota storage",
        "Team worker capability allow-list",
        "agent_create",
        "TeamWorkerPermissionPolicy(allowed_capabilities=...)",
        "WorkspaceToolSandboxPolicy",
        "large-module decomposition",
        "a2a_operations.py",
        "planner.py",
        "team.py",
        "asgi.py",
    ]:
        assert_phrase(backlog, expected)

    for expected in [
        "production A2A reference services should pass",
        "A2AOperationServer(rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy",
        "agent_create must be paired with an explicit worker capability allow-list",
        "TeamWorkerPermissionPolicy(allowed_capabilities=",
    ]:
        assert_phrase(production_readiness, expected)

    assert chr(0x9239) not in skill
    assert chr(0x251C) not in skill
    assert chr(0x2514) not in skill
    assert chr(0x2500) not in skill
    assert "+-- runtime/" in skill
    assert "`TeamWorkerPermissionPolicy(allowed_capabilities=...)`" in skill
