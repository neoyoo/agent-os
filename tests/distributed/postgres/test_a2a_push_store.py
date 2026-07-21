from __future__ import annotations

import pytest

from agentos.distributed.a2a_models import (
    A2APushConfigRecord,
    A2APushConfigRecordPage,
)
from agentos.distributed.errors import (
    A2APushConfigNotFoundError,
    A2APushConflictError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres.a2a import PostgresA2APushStore
from agentos.runtime.payloads import ProtectedPayloadRef
from tests.distributed.postgres._a2a_push_fake import BINDING, Database, SCOPE
from tests.planning._async import async_test


def _record(
    *,
    config_id: str = "push_1",
    url: str = "https://push.example.test/a2a",
    token: str = "ciphertext-1",
    digest: str = "digest-1",
) -> A2APushConfigRecord:
    return A2APushConfigRecord(
        tenant_id="tenant_1",
        task_id="run_1",
        config_id=config_id,
        url=url,
        authentication_scheme="Bearer",
        secret_ref=ProtectedPayloadRef(token, digest),
    )


def _v1_record() -> A2APushConfigRecord:
    return A2APushConfigRecord(
        tenant_id="tenant_1",
        task_id="run_1",
        config_id="push_1",
        url="https://push.example.test/a2a",
        authentication_scheme="Bearer",
        secret_ref=ProtectedPayloadRef("ciphertext-1", "digest-1"),
    )


@async_test
async def test_push_create_persists_v1_singular_authentication() -> None:
    database = Database()
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]

    created = await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_v1_record(),
        operation_id="a2a_op_1",
    )

    assert created.authentication_scheme == "Bearer"
    operation = database.connection_value.operations[("tenant_1", "a2a_op_1")]
    assert operation["operation_kind"] == "create"
    assert operation["result_authentication_scheme"] == "Bearer"


@async_test
async def test_push_create_enqueues_one_reconciliation_webhook() -> None:
    database = Database()
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]

    first = await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="request_1",
    )
    duplicate = await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(token="different-random-ciphertext"),
        operation_id="request_1",
    )

    assert duplicate == first
    assert duplicate.secret_ref == ProtectedPayloadRef("ciphertext-1", "digest-1")
    assert len(database.connection_value.outbox_payloads) == 1
    assert set(database.connection_value.outbox_payloads[0]) == {"delivery_id"}


@async_test
async def test_push_create_rejects_operation_identity_conflict() -> None:
    store = PostgresA2APushStore(Database())  # type: ignore[arg-type]
    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="request_1",
    )

    with pytest.raises(A2APushConflictError):
        await store.create(
            scope=SCOPE,
            binding=BINDING,
            record=_record(url="https://other.example.test/a2a"),
            operation_id="request_1",
        )


@async_test
async def test_push_get_list_delete_are_tenant_scoped_and_idempotent() -> None:
    database = Database()
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]
    record = await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="request_1",
    )
    reconciliation_outbox = database.connection_value.outbox_payloads

    assert await store.get(
        scope=SCOPE,
        task_id="run_1",
        config_id="push_1",
    ) == record
    assert await store.list(
        scope=SCOPE,
        task_id="run_1",
        page_size=50,
        page_token=None,
    ) == A2APushConfigRecordPage(records=(record,), next_page_token="")
    assert (
        await store.get(
            scope=RequestScope("tenant_2", "principal_1"),
            task_id="run_1",
            config_id="push_1",
        )
        is None
    )

    await store.delete(
        scope=SCOPE,
        task_id="run_1",
        config_id="push_1",
        operation_id="a2a_op_2",
    )
    await store.delete(
        scope=SCOPE,
        task_id="run_1",
        config_id="push_1",
        operation_id="a2a_op_2",
    )
    await store.delete(
        scope=SCOPE,
        task_id="run_1",
        config_id="push_1",
        operation_id="a2a_op_3",
    )
    with pytest.raises(A2APushConfigNotFoundError):
        await store.delete(
            scope=SCOPE,
            task_id="run_1",
            config_id="push_never_existed",
            operation_id="a2a_op_4",
        )
    assert database.connection_value.outbox_payloads == reconciliation_outbox


@async_test
async def test_push_list_uses_scope_bound_keyset_pagination() -> None:
    database = Database()
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]
    for index in range(1, 4):
        await store.create(
            scope=SCOPE,
            binding=BINDING,
            record=_record(config_id=f"push_{index}"),
            operation_id=f"create_{index}",
        )

    first = await store.list(
        scope=SCOPE,
        task_id="run_1",
        page_size=2,
        page_token=None,
    )
    assert [record.config_id for record in first.records] == ["push_1", "push_2"]
    assert first.next_page_token

    second = await store.list(
        scope=SCOPE,
        task_id="run_1",
        page_size=2,
        page_token=first.next_page_token,
    )
    assert [record.config_id for record in second.records] == ["push_3"]
    assert second.next_page_token == ""

    with pytest.raises(ValueError, match="page token"):
        await store.list(
            scope=RequestScope("tenant_2", "principal_1"),
            task_id="run_1",
            page_size=2,
            page_token=first.next_page_token,
        )


def test_runtime_schema_contains_push_config_operation_and_outbox_truth() -> None:
    from agentos.distributed.postgres.schema import SCHEMA_STATEMENTS

    schema = " ".join("\n".join(SCHEMA_STATEMENTS).lower().split())

    assert "agentos_distributed_a2a_push_configs" in schema
    assert "primary key (tenant_id, task_id, config_id)" in schema
    assert "agentos_distributed_a2a_push_operations" in schema
    assert "primary key (tenant_id, operation_id)" in schema
