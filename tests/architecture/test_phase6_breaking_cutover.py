from __future__ import annotations

import importlib
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_phase6_legacy_modules_are_physically_removed() -> None:
    removed = (
        "src/agentos/channels/a2a.py",
        "src/agentos/channels/a2a_operations.py",
        "src/agentos/channels/a2a_conformance.py",
        "src/agentos/channels/asgi.py",
        "src/agentos/channels/durable_session.py",
        "src/agentos/deployment.py",
        "src/agentos/multi/team.py",
        "src/agentos/runtime/profile_distributed.py",
    )

    assert [path for path in removed if (PROJECT_ROOT / path).exists()] == []


def test_phase6_canonical_cutover_modules_exist() -> None:
    for module_name in (
        "agentos.channels.asgi_app",
        "agentos.channels.a2a_endpoint",
        "agentos.distributed.profile",
        "agentos.multi.team_runtime",
        "agentos.multi.team_tools",
        "agentos.deployment_types",
        "agentos.deployment_reports",
        "agentos.deployment_validation",
        "agentos.deployment_profiles",
        "agentos.deployment_workers",
    ):
        importlib.import_module(module_name)


def test_phase6_public_facades_exclude_legacy_surfaces() -> None:
    channels = importlib.import_module("agentos.channels")
    multi = importlib.import_module("agentos.multi")

    assert not hasattr(channels, "__getattr__")
    assert not hasattr(multi, "__getattr__")
    assert set(channels.__all__).isdisjoint(
        {
            "A2AAdapter",
            "A2AOperationServer",
            "A2APushNotificationDaemon",
            "AsgiAgentApp",
            "DurableAgentSessionProvider",
            "LeaseFencedSessionPersistence",
            "RedisSessionLeaseStore",
            "SessionLeaseStore",
        },
    )
    assert set(multi.__all__).isdisjoint(
        {
            "PostgresTaskStore",
            "PostgresTeamStore",
            "RedisAgentMessageQueue",
            "TeamWorkerDaemon",
            "TeamWorkerRunner",
        },
    )


def test_phase6_distributed_facade_exposes_approved_high_level_entries() -> None:
    distributed = importlib.import_module("agentos.distributed")
    supervisor = importlib.import_module("agentos.distributed.worker.supervisor")

    assert set(distributed.__all__) == {
        "DistributedRuntimeProfile",
        "DistributedWorker",
    }
    assert distributed.DistributedWorker is supervisor.DistributedWorker


def test_phase6_legacy_backend_extras_are_removed() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    extras = project["project"]["optional-dependencies"]

    assert "postgres" not in extras
    assert "redis" not in extras
    assert extras["distributed"] == [
        "psycopg[binary]>=3.1",
        "psycopg-pool>=3.2",
        "redis>=5.0",
    ]
