from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from importlib.resources import files

import pytest

from agentos.distributed.migrations.models import MigrationPlan, MigrationReport
from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from tests.planning._async import async_test


def test_canonical_plan_uses_explicit_ordered_catalog_and_exact_checksums() -> None:
    plan = canonical_migration_plan()

    assert plan.target_version == 2
    assert [(entry.version, entry.name) for entry in plan.entries] == [
        (1, "2026-07-20-postgres-distributed-runtime.sql"),
        (2, "2026-07-21-postgres-team-delivery.sql"),
    ]
    for entry in plan.entries:
        source = files("agentos.migrations").joinpath(entry.name).read_bytes()
        normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        up = normalized.split(b"-- migrate:up\n", 1)[1].split(
            b"-- migrate:down\n",
            1,
        )[0]
        assert entry.sha256 == sha256(up).hexdigest()
        assert entry.up_sql.encode("utf-8") == up


def test_migration_contract_is_immutable() -> None:
    plan = canonical_migration_plan()

    with pytest.raises(FrozenInstanceError):
        plan.target_version = 3  # type: ignore[misc]


def test_migration_plan_rejects_non_contiguous_catalog() -> None:
    entries = canonical_migration_plan().entries

    with pytest.raises(ValueError, match="contiguous"):
        MigrationPlan(entries=(entries[1],), target_version=2)


def test_migration_report_requires_consistent_changed_flag() -> None:
    with pytest.raises(ValueError, match="changed"):
        MigrationReport(
            current_version=2,
            target_version=2,
            applied_versions=(),
            changed=True,
        )


class _Port:
    def __init__(self) -> None:
        self.calls: list[tuple[str, MigrationPlan]] = []

    async def check(self, plan: MigrationPlan) -> MigrationReport:
        self.calls.append(("check", plan))
        return MigrationReport(
            current_version=plan.target_version,
            target_version=plan.target_version,
            applied_versions=(),
            changed=False,
        )

    async def apply(self, plan: MigrationPlan) -> MigrationReport:
        self.calls.append(("apply", plan))
        return MigrationReport(
            current_version=plan.target_version,
            target_version=plan.target_version,
            applied_versions=tuple(entry.version for entry in plan.entries),
            changed=True,
        )


@async_test
async def test_service_owns_plan_and_delegates_check_and_apply() -> None:
    port = _Port()
    plan = canonical_migration_plan()
    service = DistributedMigrationService(port=port, plan=plan)

    checked = await service.check()
    applied = await service.apply()

    assert checked.changed is False
    assert applied.applied_versions == (1, 2)
    assert port.calls == [("check", plan), ("apply", plan)]
