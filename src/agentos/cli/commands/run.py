from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any, TextIO

from agentos._json_values import thaw_json_value
from agentos.cli.application import CliHostFactory, CliServiceHost
from agentos.cli.auth import CliResource, resolve_cli_scope
from agentos.cli.output import write_json_line, write_jsonl_record
from agentos.distributed.models import (
    RunReadModel,
    RunSubmission,
    StreamGap,
)
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.transports.run_stream import (
    decode_cursor,
    is_terminal_event,
    project_replay_item,
    project_stream_gap_projection,
)


async def run_run_command(
    args: argparse.Namespace,
    host_factory: CliHostFactory,
    *,
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    """通过 service host 边界执行一个 Run CLI 操作。"""

    async with host_factory.open_service_host() as host:
        if args.action == "submit":
            await _submit(args, host, stdin=stdin, stdout=stdout)
        elif args.action == "command":
            await _command(args, host, stdout=stdout)
        elif args.action == "get":
            await _get(args, host, stdout=stdout)
        elif args.action == "watch":
            await _watch(args, host, stdout=stdout)
        else:
            raise ValueError("run action is invalid")


async def _submit(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "run_submit",
        CliResource(args.session_id),
        args.tenant,
    )
    content = _submission_content(args, stdin)
    receipt = await host.runs.submit(
        scope,
        RunSubmission(
            args.session_id,
            args.submission_id,
            content,
            tuple(args.artifact),
        ),
    )
    write_json_line(
        {
            "session_id": receipt.session_id,
            "run_id": receipt.run_id,
            "submission_id": receipt.submission_id,
            "aggregate_version": receipt.aggregate_version,
            "duplicate": receipt.duplicate,
        },
        stream=stdout,
    )


async def _command(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    operation = (
        "side_effect_resolve"
        if args.kind == "resolve_side_effect"
        else "run_command"
    )
    scope = await resolve_cli_scope(
        host.scope_resolver,
        operation,
        CliResource(args.session_id, run_id=args.run_id),
        args.tenant,
    )
    payload = _command_payload(args)
    receipt = await host.commands.submit(
        scope,
        args.session_id,
        DurableRunCommand(
            args.run_id,
            args.command_id,
            args.kind,
            payload,
        ),
    )
    write_json_line(
        {
            "run_id": receipt.run_id,
            "command_id": receipt.command_id,
            "kind": receipt.kind,
            "aggregate_version": receipt.aggregate_version,
            "duplicate": receipt.duplicate,
        },
        stream=stdout,
    )


async def _get(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "run_query",
        CliResource(args.session_id, run_id=args.run_id),
        args.tenant,
    )
    model = await host.queries.get(scope, args.session_id, args.run_id)
    write_json_line(_run_read_payload(model), stream=stdout)


async def _watch(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "run_watch",
        CliResource(args.session_id, run_id=args.run_id),
        args.tenant,
    )
    cursor = (
        None
        if args.cursor is None
        else decode_cursor(
            args.cursor,
            tenant_id=scope.tenant_id,
            session_id=args.session_id,
            run_id=args.run_id,
        )
    )
    subscription = await host.events.subscribe(
        scope,
        args.session_id,
        args.run_id,
        cursor,
    )
    try:
        async for item in subscription:
            if type(item) is StreamGap:
                gap = project_stream_gap_projection(item)
                write_jsonl_record(
                    {
                        "type": "stream_gap",
                        "session_id": gap.session_id,
                        "run_id": gap.run_id,
                        "reason": gap.reason,
                    },
                    stream=stdout,
                )
                return
            projection = project_replay_item(item)
            write_jsonl_record(
                {
                    "type": "event",
                    "session_id": item.event.session_id,
                    "run_id": item.event.run_id,
                    "cursor": projection.cursor,
                    "event_kind": projection.event.kind,
                    "data": thaw_json_value(projection.data),
                },
                stream=stdout,
            )
            if is_terminal_event(item.event.event):
                return
        raise RuntimeError("run event stream ended unexpectedly")
    finally:
        await subscription.aclose()


def _submission_content(args: argparse.Namespace, stdin: TextIO) -> str:
    selections = (
        args.content is not None,
        args.content_file is not None,
        args.stdin is True,
    )
    if sum(selections) != 1:
        raise ValueError("exactly one run content source is required")
    if args.content is not None:
        return args.content
    if args.content_file is not None:
        return Path(args.content_file).read_text(encoding="utf-8")
    return stdin.read()


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    sources = (args.payload_json is not None, args.payload_file is not None)
    if all(sources):
        raise ValueError("command payload sources are mutually exclusive")
    if not any(sources):
        if args.kind in {"hitl_answer", "resolve_side_effect"}:
            raise ValueError("command payload is required")
        return {}
    text = (
        args.payload_json
        if args.payload_json is not None
        else Path(args.payload_file).read_text(encoding="utf-8")
    )
    return _parse_json_object(text)


def _parse_json_object(text: str) -> dict[str, object]:
    if type(text) is not str:
        raise TypeError("JSON payload must be text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("command payload must be valid JSON") from error
    if type(value) is not dict:
        raise ValueError("command payload must be a JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _run_read_payload(model: RunReadModel) -> dict[str, object]:
    reason = model.wait_reason
    return {
        "session_id": model.session_id,
        "run_id": model.run_id,
        "status": model.status.value,
        "aggregate_version": model.aggregate_version,
        "wait_reason": None
        if reason is None
        else {
            "kind": reason.kind,
            "handle": reason.handle,
            "not_before": _wire_datetime(reason.not_before),
        },
        "result": None if model.result is None else {"content": model.result.content},
    }


def _wire_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = ["run_run_command"]
