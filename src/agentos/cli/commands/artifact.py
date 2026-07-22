from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
from typing import BinaryIO, TextIO

from agentos.artifacts.types import (
    ArtifactPage,
    ArtifactRecord,
    ArtifactTooLargeError,
)
from agentos.cli.application import CliHostFactory, CliServiceHost
from agentos.cli.auth import CliResource, resolve_cli_scope
from agentos.cli.output import write_json_line, write_raw_bytes


async def run_artifact_command(
    args: argparse.Namespace,
    host_factory: CliHostFactory,
    *,
    stdout: TextIO,
    binary_stdout: BinaryIO,
) -> None:
    """通过 service host 边界执行一个 Artifact CLI 操作。"""

    async with host_factory.open_service_host() as host:
        if args.action == "upload":
            await _upload(args, host, stdout=stdout)
        elif args.action == "list":
            await _list(args, host, stdout=stdout)
        elif args.action == "read":
            await _read(
                args,
                host,
                stdout=stdout,
                binary_stdout=binary_stdout,
            )
        elif args.action == "delete":
            await _delete(args, host, stdout=stdout)
        else:
            raise ValueError("artifact action is invalid")


async def _upload(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "artifact_upload",
        CliResource(args.session_id),
        args.tenant,
    )
    path = Path(args.file)
    limit = host.artifacts.policy.max_size_bytes
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ArtifactTooLargeError()
    record = await host.artifacts.upload(
        scope,
        args.session_id,
        args.upload_id,
        data,
        path.name,
        args.media_type,
    )
    write_json_line(_artifact_payload(record), stream=stdout)


async def _list(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "artifact_list",
        CliResource(args.session_id),
        args.tenant,
    )
    limit = 20 if args.limit is None else args.limit
    page = await host.artifacts.list(
        scope,
        args.session_id,
        args.cursor,
        limit,
    )
    write_json_line(_artifact_page_payload(page), stream=stdout)


async def _read(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
    binary_stdout: BinaryIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "artifact_read",
        CliResource(args.session_id, artifact_id=args.artifact_id),
        args.tenant,
    )
    content = await host.artifacts.read(
        scope,
        args.session_id,
        args.artifact_id,
    )
    if args.output == "-":
        write_raw_bytes(content.data, stream=binary_stdout)
        return
    _atomic_write(Path(args.output), content.data)
    write_json_line(_artifact_payload(content.record), stream=stdout)


async def _delete(
    args: argparse.Namespace,
    host: CliServiceHost,
    *,
    stdout: TextIO,
) -> None:
    scope = await resolve_cli_scope(
        host.scope_resolver,
        "artifact_delete",
        CliResource(args.session_id, artifact_id=args.artifact_id),
        args.tenant,
    )
    await host.artifacts.delete(
        scope,
        args.session_id,
        args.artifact_id,
        args.deletion_id,
    )
    write_json_line(
        {
            "session_id": args.session_id,
            "artifact_id": args.artifact_id,
            "deletion_id": args.deletion_id,
            "deleted": True,
        },
        stream=stdout,
    )


def _artifact_page_payload(page: ArtifactPage) -> dict[str, object]:
    return {
        "items": [_artifact_payload(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


def _artifact_payload(record: ArtifactRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "session_id": record.session_id,
        "filename": record.filename,
        "media_type": record.media_type,
        "size_bytes": record.size_bytes,
        "created_at": _wire_datetime(record.created_at),
    }


def _wire_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _atomic_write(destination: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["run_artifact_command"]
