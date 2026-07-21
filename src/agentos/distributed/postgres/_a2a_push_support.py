from __future__ import annotations

import base64
from collections.abc import Mapping
from hashlib import sha256
import json
from typing import cast

from agentos.distributed.a2a_models import A2APushConfigRecord, A2ATaskBinding
from agentos.distributed.errors import (
    A2APushConflictError,
    A2ATaskConflictError,
    A2ATaskNotFoundError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import (
    AsyncConnection,
    Row,
    fetchone,
)
from agentos.runtime.payloads import ProtectedPayloadRef


async def get_push_operation(
    connection: AsyncConnection,
    scope: RequestScope,
    operation_id: str,
) -> Row | None:
    return await fetchone(
        connection,
        """
        SELECT tenant_id, operation_id, operation_kind, task_id, config_id,
               input_digest, result_url, result_authentication_scheme,
               result_secret_token, result_secret_digest
        FROM agentos_distributed_a2a_push_operations
        WHERE tenant_id = %s AND operation_id = %s
        """,
        (scope.tenant_id, operation_id),
    )


async def has_push_delete_tombstone(
    connection: AsyncConnection,
    scope: RequestScope,
    task_id: str,
    config_id: str,
) -> bool:
    row = await fetchone(
        connection,
        """
        SELECT 1
        FROM agentos_distributed_a2a_push_operations
        WHERE tenant_id = %s AND task_id = %s AND config_id = %s
          AND operation_kind = 'delete'
        LIMIT 1
        """,
        (scope.tenant_id, task_id, config_id),
    )
    return row is not None


async def insert_push_operation(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    operation_id: str,
    kind: str,
    task_id: str,
    config_id: str,
    input_digest: str,
    record: A2APushConfigRecord | None,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_distributed_a2a_push_operations
            (tenant_id, operation_id, operation_kind, task_id, config_id,
             input_digest, result_url, result_authentication_scheme,
             result_secret_token, result_secret_digest)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope.tenant_id,
            operation_id,
            kind,
            task_id,
            config_id,
            input_digest,
            None if record is None else record.url,
            None if record is None else record.authentication_scheme,
            None if record is None else secret_token(record),
            None if record is None else secret_digest(record),
        ),
    )


async def require_push_binding(
    connection: AsyncConnection,
    binding: A2ATaskBinding,
) -> None:
    current = await lock_push_binding(connection, binding.tenant_id, binding.task_id)
    if current is None:
        raise A2ATaskNotFoundError()
    if current != binding:
        raise A2ATaskConflictError()


async def find_push_binding(
    connection: AsyncConnection,
    tenant_id: str,
    task_id: str,
) -> A2ATaskBinding | None:
    row = await fetchone(
        connection,
        """
        SELECT tenant_id, task_id, session_id, run_id
        FROM agentos_distributed_a2a_tasks
        WHERE tenant_id = %s AND task_id = %s
        """,
        (tenant_id, task_id),
    )
    return None if row is None else _binding_from_row(row)


async def lock_push_binding(
    connection: AsyncConnection,
    tenant_id: str,
    task_id: str,
) -> A2ATaskBinding | None:
    row = await fetchone(
        connection,
        """
        SELECT tenant_id, task_id, session_id, run_id
        FROM agentos_distributed_a2a_tasks
        WHERE tenant_id = %s AND task_id = %s
        FOR UPDATE
        """,
        (tenant_id, task_id),
    )
    return None if row is None else _binding_from_row(row)


def validate_push_operation(
    row: Row,
    *,
    kind: str,
    task_id: str,
    config_id: str,
    input_digest: str,
) -> None:
    if (
        row["operation_kind"] != kind
        or row["task_id"] != task_id
        or row["config_id"] != config_id
        or row["input_digest"] != input_digest
    ):
        raise A2APushConflictError()


def push_record_from_operation(row: Row) -> A2APushConfigRecord:
    url = row["result_url"]
    scheme = row["result_authentication_scheme"]
    if type(url) is not str or (scheme is not None and type(scheme) is not str):
        raise A2APushConflictError()
    return _build_push_record(
        tenant_id=cast(str, row["tenant_id"]),
        task_id=cast(str, row["task_id"]),
        config_id=cast(str, row["config_id"]),
        url=url,
        scheme=cast(str | None, scheme),
        secret_token_value=row["result_secret_token"],
        secret_digest_value=row["result_secret_digest"],
    )


def push_record_from_row(row: Row) -> A2APushConfigRecord:
    scheme = row["authentication_scheme"]
    if scheme is not None and type(scheme) is not str:
        raise A2APushConflictError()
    return _build_push_record(
        tenant_id=cast(str, row["tenant_id"]),
        task_id=cast(str, row["task_id"]),
        config_id=cast(str, row["config_id"]),
        url=cast(str, row["url"]),
        scheme=cast(str | None, scheme),
        secret_token_value=row["secret_token"],
        secret_digest_value=row["secret_digest"],
    )


def validate_push_input(
    scope: object,
    binding: object,
    record: object,
) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(binding) is not A2ATaskBinding:
        raise TypeError("binding must be A2ATaskBinding")
    if type(record) is not A2APushConfigRecord:
        raise TypeError("record must be A2APushConfigRecord")
    if (
        binding.tenant_id != scope.tenant_id
        or record.tenant_id != scope.tenant_id
        or record.task_id != binding.task_id
    ):
        raise ValueError("a2a push config scope is invalid")


def require_push_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def secret_token(record: A2APushConfigRecord) -> str | None:
    reference = record.secret_ref
    return None if reference is None else reference.token


def secret_digest(record: A2APushConfigRecord) -> str | None:
    reference = record.secret_ref
    return None if reference is None else reference.digest


def create_push_digest(record: A2APushConfigRecord) -> str:
    return _digest(
        {
            "authentication_scheme": record.authentication_scheme,
            "config_id": record.config_id,
            "kind": "create",
            "secret_digest": secret_digest(record),
            "task_id": record.task_id,
            "tenant_id": record.tenant_id,
            "url": record.url,
            "version": 1,
        },
    )


def delete_push_digest(scope: RequestScope, task_id: str, config_id: str) -> str:
    return _digest(
        {
            "config_id": config_id,
            "kind": "delete",
            "task_id": task_id,
            "tenant_id": scope.tenant_id,
            "version": 1,
        },
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def encode_push_page_token(
    scope: RequestScope,
    task_id: str,
    page_size: int,
    after_config_id: str,
) -> str:
    raw = canonical_json(
        {
            "after": after_config_id,
            "page_size": page_size,
            "scope": _push_scope_digest(scope),
            "task_id": task_id,
            "version": 1,
        },
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_push_page_token(
    token: str,
    scope: RequestScope,
    task_id: str,
    page_size: int,
) -> str:
    try:
        if type(token) is not str or not token or len(token) > 1024:
            raise ValueError
        padding = b"=" * (-len(token) % 4)
        data = json.loads(
            base64.b64decode(
                token.encode("ascii") + padding,
                altchars=b"-_",
                validate=True,
            ).decode("utf-8"),
        )
        expected_keys = {"after", "page_size", "scope", "task_id", "version"}
        if (
            type(data) is not dict
            or set(data) != expected_keys
            or type(data["after"]) is not str
            or not data["after"]
            or type(data["page_size"]) is not int
            or data["page_size"] != page_size
            or data["scope"] != _push_scope_digest(scope)
            or data["task_id"] != task_id
            or data["version"] != 1
            or encode_push_page_token(
                scope,
                task_id,
                page_size,
                data["after"],
            )
            != token
        ):
            raise ValueError
        return cast(str, data["after"])
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("a2a push page token is invalid") from None


def _push_scope_digest(scope: RequestScope) -> str:
    return sha256(
        canonical_json({"tenant_id": scope.tenant_id, "version": 1}).encode("utf-8"),
    ).hexdigest()


def _build_push_record(
    *,
    tenant_id: str,
    task_id: str,
    config_id: str,
    url: str,
    scheme: str | None,
    secret_token_value: object,
    secret_digest_value: object,
) -> A2APushConfigRecord:
    if (secret_token_value is None) != (secret_digest_value is None):
        raise A2APushConflictError()
    if secret_token_value is not None and (
        type(secret_token_value) is not str or type(secret_digest_value) is not str
    ):
        raise A2APushConflictError()
    secret_ref = (
        None
        if secret_token_value is None
        else ProtectedPayloadRef(
            secret_token_value,
            cast(str, secret_digest_value),
        )
    )
    return A2APushConfigRecord(
        tenant_id=tenant_id,
        task_id=task_id,
        config_id=config_id,
        url=url,
        authentication_scheme=scheme,
        secret_ref=secret_ref,
    )


def _binding_from_row(row: Row) -> A2ATaskBinding:
    return A2ATaskBinding(
        tenant_id=cast(str, row["tenant_id"]),
        task_id=cast(str, row["task_id"]),
        session_id=cast(str, row["session_id"]),
        run_id=cast(str, row["run_id"]),
    )


def _digest(value: Mapping[str, object]) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "canonical_json",
    "create_push_digest",
    "delete_push_digest",
    "decode_push_page_token",
    "encode_push_page_token",
    "find_push_binding",
    "get_push_operation",
    "has_push_delete_tombstone",
    "insert_push_operation",
    "lock_push_binding",
    "push_record_from_operation",
    "push_record_from_row",
    "require_push_binding",
    "require_push_scope",
    "secret_digest",
    "secret_token",
    "validate_push_input",
    "validate_push_operation",
]
