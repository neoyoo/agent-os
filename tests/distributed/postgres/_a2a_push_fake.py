from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json

from agentos.distributed.a2a_models import A2ATaskBinding
from agentos.distributed.models import RequestScope


SCOPE = RequestScope("tenant_1", "principal_1")
BINDING = A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1")
DATABASE_NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = [] if rows is None else rows

    async def fetchone(self) -> dict[str, object] | None:
        return None if not self._rows else self._rows[0]

    async def fetchall(self) -> list[dict[str, object]]:
        return list(self._rows)


class PushConnection:
    def __init__(self) -> None:
        self.tasks = {("tenant_1", "run_1"): BINDING}
        self.sessions = {("tenant_1", "session_1")}
        self.runs: dict[tuple[str, str, str], dict[str, object]] = {
            ("tenant_1", "session_1", "run_1"): {
                "tenant_id": "tenant_1",
                "session_id": "session_1",
                "run_id": "run_1",
                "status": "queued",
                "wait_kind": None,
                "aggregate_version": 1,
                "database_now": DATABASE_NOW,
            },
        }
        self.configs: dict[tuple[str, str, str], dict[str, object]] = {}
        self.operations: dict[tuple[str, str], dict[str, object]] = {}
        self.outboxes: dict[str, dict[str, object]] = {}
        self.deliveries: dict[str, dict[str, object]] = {}
        self.lock_log: list[str] = []

    @property
    def outbox_payloads(self) -> list[dict[str, object]]:
        return [dict(row["payload"]) for row in self.outboxes.values()]  # type: ignore[arg-type]

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Cursor:
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("select pg_advisory_xact_lock"):
            return Cursor()
        if "from agentos_distributed_sessions" in normalized:
            tenant_id, session_id = map(str, params)
            if (tenant_id, session_id) not in self.sessions:
                return Cursor()
            if "for update" in normalized:
                self.lock_log.append("session")
            return Cursor(
                [
                    {
                        "fencing_token": 0,
                        "session_fencing_token": 0,
                        "active_claim_id": None,
                        "active_claim_run_id": None,
                        "active_claim_expires_at": None,
                    },
                ],
            )
        if "from agentos_distributed_runs" in normalized:
            tenant_id, session_id, run_id = map(str, params)
            row = self.runs.get((tenant_id, session_id, run_id))
            if row is not None and "for update" in normalized:
                self.lock_log.append("run")
            return Cursor([] if row is None else [{**row, "database_now": DATABASE_NOW}])
        if normalized.startswith("with task as"):
            return self._list_page(params)
        if "from agentos_distributed_a2a_push_operations" in normalized:
            return self._push_operation(normalized, params)
        if "from agentos_distributed_a2a_tasks as task" in normalized:
            tenant_id, session_id, run_id = params
            rows = [
                row
                for row in self.configs.values()
                if row["tenant_id"] == tenant_id
                and row["task_id"] == run_id
                and self.tasks[(str(tenant_id), str(run_id))].session_id == session_id
            ]
            return Cursor(
                [
                    {
                        **row,
                        "session_id": session_id,
                    }
                    for row in rows
                ],
            )
        if "from agentos_distributed_a2a_tasks" in normalized:
            tenant_id, task_id = map(str, params)
            binding = self.tasks.get((tenant_id, task_id))
            if binding is not None and "for update" in normalized:
                self.lock_log.append("binding")
            return Cursor([] if binding is None else [_binding_row(binding)])
        if normalized.startswith("insert into agentos_distributed_a2a_push_configs"):
            return self._insert_config(params)
        if normalized.startswith("insert into agentos_distributed_a2a_push_operations"):
            self._insert_operation(params)
            return Cursor()
        if normalized.startswith("insert into agentos_distributed_outbox"):
            return self._insert_outbox(params)
        if normalized.startswith("insert into agentos_distributed_a2a_push_deliveries"):
            self._insert_delivery(params)
            return Cursor()
        if normalized.startswith("delete from agentos_distributed_a2a_push_configs"):
            tenant_id, task_id, config_id = map(str, params)
            self.configs.pop((tenant_id, task_id, config_id), None)
            return Cursor()
        if normalized.startswith("update agentos_distributed_a2a_push_deliveries"):
            tenant_id, task_id, config_id = map(str, params[-3:])
            for row in self.deliveries.values():
                if (
                    row["tenant_id"] == tenant_id
                    and row["task_id"] == task_id
                    and row["config_id"] == config_id
                    and row["delivered_at"] is None
                    and row["abandoned_at"] is None
                ):
                    row["suppressed_at"] = DATABASE_NOW
                    row["attempt_id"] = None
                    row["attempt_owner_id"] = None
                    row["attempt_expires_at"] = None
                    row["next_attempt_at"] = None
            return Cursor()
        if "from agentos_distributed_a2a_push_configs" in normalized:
            tenant_id, task_id, config_id = map(str, params)
            row = self.configs.get((tenant_id, task_id, config_id))
            if row is not None and "for update" in normalized:
                self.lock_log.append("config")
            return Cursor([] if row is None else [row])
        if "from agentos_distributed_a2a_push_deliveries" in normalized:
            tenant_id, task_id, config_id = map(str, params)
            rows = [
                row
                for row in self.deliveries.values()
                if row["tenant_id"] == tenant_id
                and row["task_id"] == task_id
                and row["config_id"] == config_id
            ]
            if "for update" in normalized:
                self.lock_log.append("delivery")
            return Cursor(rows)
        raise AssertionError(f"unexpected query: {normalized}")

    def _list_page(self, params: Sequence[object]) -> Cursor:
        tenant_id, task_id, after_id, _, limit = params
        task_key = (str(tenant_id), str(task_id))
        if task_key not in self.tasks:
            return Cursor([{"task_exists": False, "config_id": None}])
        rows = sorted(
            (
                row
                for (tenant, task, config_id), row in self.configs.items()
                if tenant == tenant_id
                and task == task_id
                and (after_id is None or config_id > after_id)
            ),
            key=lambda row: str(row["config_id"]),
        )[: int(limit)]
        if not rows:
            return Cursor([{"task_exists": True, "config_id": None}])
        return Cursor([{"task_exists": True, **row} for row in rows])

    def _push_operation(
        self,
        normalized: str,
        params: Sequence[object],
    ) -> Cursor:
        if "operation_kind = 'delete'" in normalized:
            tenant_id, task_id, config_id = params
            rows = [
                row
                for (tenant, _), row in self.operations.items()
                if tenant == tenant_id
                and row["task_id"] == task_id
                and row["config_id"] == config_id
                and row["operation_kind"] == "delete"
            ]
            return Cursor(rows[:1])
        tenant_id, operation_id = map(str, params)
        row = self.operations.get((tenant_id, operation_id))
        return Cursor([] if row is None else [row])

    def _insert_config(self, params: Sequence[object]) -> Cursor:
        tenant_id, task_id, config_id, url, scheme, token, digest = params
        key = (str(tenant_id), str(task_id), str(config_id))
        if key in self.configs:
            return Cursor()
        row = {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "config_id": config_id,
            "url": url,
            "authentication_scheme": scheme,
            "secret_token": token,
            "secret_digest": digest,
        }
        self.configs[key] = row
        self.lock_log.append("config")
        return Cursor([row])

    def _insert_operation(self, params: Sequence[object]) -> None:
        (
            tenant_id,
            operation_id,
            operation_kind,
            task_id,
            config_id,
            input_digest,
            result_url,
            scheme,
            token,
            digest,
        ) = params
        self.operations[(str(tenant_id), str(operation_id))] = {
            "tenant_id": tenant_id,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "task_id": task_id,
            "config_id": config_id,
            "input_digest": input_digest,
            "result_url": result_url,
            "result_authentication_scheme": scheme,
            "result_secret_token": token,
            "result_secret_digest": digest,
        }

    def _insert_outbox(self, params: Sequence[object]) -> Cursor:
        outbox_id, tenant_id, principal_id, session_id, run_id, topic, payload = params
        identifier = str(outbox_id)
        if identifier in self.outboxes:
            return Cursor()
        self.outboxes[identifier] = {
            "outbox_id": identifier,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "session_id": session_id,
            "run_id": run_id,
            "topic": topic,
            "payload": json.loads(str(payload)),
        }
        return Cursor([{"outbox_id": identifier}])

    def _insert_delivery(self, params: Sequence[object]) -> None:
        (
            tenant_id,
            principal_id,
            delivery_id,
            outbox_id,
            task_id,
            context_id,
            config_id,
            url,
            scheme,
            token,
            digest,
            event_id,
            protocol_version,
            status_sequence,
            task_state,
        ) = params
        self.deliveries.setdefault(
            str(outbox_id),
            {
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "delivery_id": delivery_id,
                "outbox_id": outbox_id,
                "task_id": task_id,
                "context_id": context_id,
                "config_id": config_id,
                "url": url,
                "authentication_scheme": scheme,
                "secret_token": token,
                "secret_digest": digest,
                "event_id": event_id,
                "protocol_version": protocol_version,
                "status_sequence": status_sequence,
                "task_state": task_state,
                "delivered_at": None,
                "suppressed_at": None,
                "abandoned_at": None,
                "attempt_id": None,
                "attempt_owner_id": None,
                "attempt_expires_at": None,
                "next_attempt_at": None,
            },
        )


class Database:
    def __init__(self) -> None:
        self.connection_value = PushConnection()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PushConnection]:
        yield self.connection_value

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[PushConnection]:
        yield self.connection_value


def _binding_row(binding: A2ATaskBinding) -> dict[str, object]:
    return {
        "tenant_id": binding.tenant_id,
        "task_id": binding.task_id,
        "session_id": binding.session_id,
        "run_id": binding.run_id,
    }


__all__ = ["BINDING", "DATABASE_NOW", "Database", "PushConnection", "SCOPE"]
