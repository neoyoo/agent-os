from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import StringIO
import json
from pathlib import Path

import pytest

from agentos._waiting import WaitReason
from agentos.cli.auth import CliResource
from agentos.cli.commands.run import run_run_command
from agentos.distributed.models import (
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
    StreamGap,
)
from agentos.runtime.durable_commands import (
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunStatus
from agentos.distributed.models import LiveContentDelta, LiveTurnCompleted
from agentos.transports.run_stream import encode_cursor


class _Resolver:
    def __init__(self, scope: RequestScope) -> None:
        self.scope = scope
        self.calls: list[tuple[str, CliResource, str]] = []

    async def resolve(
        self,
        operation: str,
        resource: CliResource,
        tenant_hint: str,
    ) -> RequestScope:
        self.calls.append((operation, resource, tenant_hint))
        return self.scope


class _Runs:
    def __init__(self) -> None:
        self.calls: list[tuple[RequestScope, RunSubmission]] = []

    async def submit(
        self,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt:
        self.calls.append((scope, submission))
        return RunSubmissionReceipt(
            submission.session_id,
            "run_1",
            submission.submission_id,
            1,
            False,
        )


class _Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[RequestScope, str, DurableRunCommand]] = []

    async def submit(
        self,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt:
        self.calls.append((scope, session_id, command))
        return DurableCommandReceipt(
            command.run_id,
            command.command_id,
            command.kind,
            2,
            True,
        )


class _Queries:
    def __init__(self, model: RunReadModel) -> None:
        self.model = model
        self.calls: list[tuple[RequestScope, str, str]] = []

    async def get(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel:
        self.calls.append((scope, session_id, run_id))
        return self.model


class _Subscription:
    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.index = 0
        self.close_calls = 0

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __anext__(self):  # type: ignore[no-untyped-def]
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item

    async def aclose(self) -> None:
        self.close_calls += 1


class _Events:
    def __init__(self, subscription: _Subscription) -> None:
        self.subscription = subscription
        self.calls: list[tuple[RequestScope, str, str, str | None]] = []

    async def subscribe(
        self,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        cursor: str | None,
    ) -> _Subscription:
        self.calls.append((scope, session_id, run_id, cursor))
        return self.subscription


class _HostFactory:
    def __init__(self, *, subscription: _Subscription | None = None) -> None:
        self.scope = RequestScope("tenant_authoritative", "cli_service")
        self.scope_resolver = _Resolver(self.scope)
        self.runs = _Runs()
        self.commands = _Commands()
        self.queries = _Queries(
            RunReadModel(
                "tenant_authoritative",
                "session_1",
                "run_1",
                RunStatus.WAITING,
                WaitReason("human_input", "approval_1", "private"),
                4,
                None,
            )
        )
        self.events = _Events(subscription or _Subscription([]))
        self.artifacts = object()
        self.entries = 0
        self.exits = 0

    @asynccontextmanager
    async def open_service_host(self):  # type: ignore[no-untyped-def]
        self.entries += 1
        try:
            yield self
        finally:
            self.exits += 1


def _args(action: str, **values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "action": action,
        "tenant": "tenant_hint",
        "session_id": "session_1",
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def test_submit_builds_dto_after_authorization_and_writes_receipt() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        stdout = StringIO()

        await run_run_command(
            _args(
                "submit",
                submission_id="submission_1",
                content="hello",
                content_file=None,
                stdin=False,
                artifact=["art_00000000-0000-4000-8000-000000000001"],
            ),
            factory,  # type: ignore[arg-type]
            stdin=StringIO("ignored"),
            stdout=stdout,
        )

        assert factory.scope_resolver.calls == [
            (
                "run_submit",
                CliResource("session_1"),
                "tenant_hint",
            )
        ]
        assert factory.runs.calls == [
            (
                factory.scope,
                RunSubmission(
                    "session_1",
                    "submission_1",
                    "hello",
                    ("art_00000000-0000-4000-8000-000000000001",),
                ),
            )
        ]
        assert json.loads(stdout.getvalue()) == {
            "aggregate_version": 1,
            "duplicate": False,
            "run_id": "run_1",
            "session_id": "session_1",
            "submission_id": "submission_1",
        }
        assert factory.entries == factory.exits == 1

    asyncio.run(scenario())


def test_submit_reads_utf8_content_file_or_injected_stdin(tmp_path: Path) -> None:
    async def scenario() -> None:
        content_file = tmp_path / "prompt.txt"
        content_file.write_text("file content", encoding="utf-8")

        for args, stdin, expected in (
            (
                _args(
                    "submit",
                    submission_id="submission_file",
                    content=None,
                    content_file=str(content_file),
                    stdin=False,
                    artifact=[],
                ),
                StringIO("ignored"),
                "file content",
            ),
            (
                _args(
                    "submit",
                    submission_id="submission_stdin",
                    content=None,
                    content_file=None,
                    stdin=True,
                    artifact=[],
                ),
                StringIO("stdin content"),
                "stdin content",
            ),
        ):
            factory = _HostFactory()
            await run_run_command(
                args,
                factory,  # type: ignore[arg-type]
                stdin=stdin,
                stdout=StringIO(),
            )
            assert factory.runs.calls[0][1].content == expected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        '[1,2]',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e999}',
        '{"value":1,"value":2}',
        '{"nested":{"value":1,"value":2}}',
    ],
)
def test_command_rejects_noncanonical_json_before_service_call(payload: str) -> None:
    async def scenario() -> None:
        factory = _HostFactory()

        with pytest.raises(ValueError):
            await run_run_command(
                _args(
                    "command",
                    run_id="run_1",
                    command_id="command_1",
                    kind="cancel",
                    payload_json=payload,
                    payload_file=None,
                ),
                factory,  # type: ignore[arg-type]
                stdin=StringIO(),
                stdout=StringIO(),
            )

        assert factory.commands.calls == []

    asyncio.run(scenario())


def test_command_uses_distinct_side_effect_authorization_and_payload_file(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        payload_file = tmp_path / "payload.json"
        payload = {
            "version": 1,
            "operation_id": "operation_00000000000000000000000000000000",
            "kind": "fail",
            "result_ref": None,
            "result_digest": None,
            "attestation_ref": None,
            "attestation_digest": None,
        }
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        factory = _HostFactory()
        stdout = StringIO()

        await run_run_command(
            _args(
                "command",
                run_id="run_1",
                command_id="command_1",
                kind="resolve_side_effect",
                payload_json=None,
                payload_file=str(payload_file),
            ),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=stdout,
        )

        assert factory.scope_resolver.calls == [
            (
                "side_effect_resolve",
                CliResource("session_1", run_id="run_1"),
                "tenant_hint",
            )
        ]
        _, session_id, command = factory.commands.calls[0]
        assert session_id == "session_1"
        assert command == DurableRunCommand(
            "run_1",
            "command_1",
            "resolve_side_effect",
            payload,
        )
        assert json.loads(stdout.getvalue()) == {
            "aggregate_version": 2,
            "command_id": "command_1",
            "duplicate": True,
            "kind": "resolve_side_effect",
            "run_id": "run_1",
        }

    asyncio.run(scenario())


def test_command_normalizes_missing_optional_payload_to_empty_object() -> None:
    async def scenario() -> None:
        factory = _HostFactory()

        await run_run_command(
            _args(
                "command",
                run_id="run_1",
                command_id="command_1",
                kind="retry",
                payload_json=None,
                payload_file=None,
            ),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=StringIO(),
        )

        assert factory.commands.calls[0][2].payload == {}

    asyncio.run(scenario())


def test_get_uses_http_allowlisted_shape_without_tenant_or_private_detail() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        stdout = StringIO()

        await run_run_command(
            _args("get", run_id="run_1"),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=stdout,
        )

        assert factory.queries.calls == [
            (factory.scope, "session_1", "run_1")
        ]
        assert json.loads(stdout.getvalue()) == {
            "aggregate_version": 4,
            "result": None,
            "run_id": "run_1",
            "session_id": "session_1",
            "status": "waiting",
            "wait_reason": {
                "handle": "approval_1",
                "kind": "human_input",
                "not_before": None,
            },
        }
        assert "tenant_authoritative" not in stdout.getvalue()
        assert "private" not in stdout.getvalue()

    asyncio.run(scenario())


def _event(sequence: int, event: object) -> ReplayItem:
    return ReplayItem(
        f"1700000000000-{sequence}",
        RunEventEnvelope(
            "tenant_authoritative",
            "session_1",
            "run_1",
            "turn_1",
            1,
            sequence,
            event,  # type: ignore[arg-type]
            datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC),
        ),
    )


def test_watch_decodes_public_cursor_and_closes_after_terminal_event() -> None:
    async def scenario() -> None:
        subscription = _Subscription(
            [
                _event(1, LiveContentDelta(0, "hello")),
                _event(2, LiveTurnCompleted()),
                _event(3, LiveContentDelta(1, "must not be consumed")),
            ]
        )
        factory = _HostFactory(subscription=subscription)
        public_cursor = encode_cursor(
            tenant_id="tenant_authoritative",
            session_id="session_1",
            run_id="run_1",
            position="1700000000000-0",
        )
        stdout = StringIO()

        await run_run_command(
            _args("watch", run_id="run_1", cursor=public_cursor),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=stdout,
        )

        assert factory.scope_resolver.calls == [
            (
                "run_watch",
                CliResource("session_1", run_id="run_1"),
                "tenant_hint",
            )
        ]
        assert factory.events.calls == [
            (factory.scope, "session_1", "run_1", "1700000000000-0")
        ]
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert [frame["type"] for frame in frames] == ["event", "event"]
        assert [frame["event_kind"] for frame in frames] == [
            "content_delta",
            "turn_completed",
        ]
        assert frames[0]["data"]["event"] == {"index": 0, "text": "hello"}
        assert frames[0]["session_id"] == "session_1"
        assert frames[0]["run_id"] == "run_1"
        assert subscription.index == 2
        assert subscription.close_calls == 1

    asyncio.run(scenario())


def test_watch_emits_gap_and_closes_without_consuming_more_items() -> None:
    async def scenario() -> None:
        gap = StreamGap(
            "tenant_authoritative",
            "session_1",
            "run_1",
            "1700000000000-0",
            "1700000000000-5",
            "trimmed",
        )
        subscription = _Subscription(
            [gap, _event(1, LiveTurnCompleted())]
        )
        factory = _HostFactory(subscription=subscription)
        stdout = StringIO()

        await run_run_command(
            _args("watch", run_id="run_1", cursor=None),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=stdout,
        )

        assert json.loads(stdout.getvalue()) == {
            "reason": "trimmed",
            "run_id": "run_1",
            "session_id": "session_1",
            "type": "stream_gap",
        }
        assert subscription.index == 1
        assert subscription.close_calls == 1

    asyncio.run(scenario())


def test_watch_rejects_unexpected_stream_end_and_closes_subscription() -> None:
    async def scenario() -> None:
        subscription = _Subscription([_event(1, LiveContentDelta(0, "hello"))])
        factory = _HostFactory(subscription=subscription)
        stdout = StringIO()

        with pytest.raises(
            RuntimeError,
            match="run event stream ended unexpectedly",
        ):
            await run_run_command(
                _args("watch", run_id="run_1", cursor=None),
                factory,  # type: ignore[arg-type]
                stdin=StringIO(),
                stdout=stdout,
            )

        assert subscription.close_calls == 1

    asyncio.run(scenario())


def test_get_completed_result_uses_only_content() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        factory.queries.model = RunReadModel(
            "tenant_authoritative",
            "session_1",
            "run_1",
            RunStatus.COMPLETED,
            None,
            5,
            AgentResult("done"),
        )
        stdout = StringIO()

        await run_run_command(
            _args("get", run_id="run_1"),
            factory,  # type: ignore[arg-type]
            stdin=StringIO(),
            stdout=stdout,
        )

        assert json.loads(stdout.getvalue())["result"] == {"content": "done"}

    asyncio.run(scenario())
