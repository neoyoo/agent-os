from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.artifacts.types import (
    ArtifactPage,
    ArtifactRecord,
    ArtifactTooLargeError,
)
from agentos.cli.auth import CliResource
from agentos.cli.commands.artifact import run_artifact_command
from agentos.distributed.models import ArtifactContent, RequestScope


ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"


def _record(*, filename: str = "drawing.png", size_bytes: int = 3) -> ArtifactRecord:
    return ArtifactRecord(
        ARTIFACT_ID,
        "session_1",
        filename,
        "image/png",
        size_bytes,
        datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
    )


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


class _Artifacts:
    def __init__(self, *, max_size_bytes: int = 4) -> None:
        self.policy = SimpleNamespace(max_size_bytes=max_size_bytes)
        self.upload_calls: list[tuple[object, ...]] = []
        self.list_calls: list[tuple[object, ...]] = []
        self.read_calls: list[tuple[object, ...]] = []
        self.delete_calls: list[tuple[object, ...]] = []
        self.read_content = ArtifactContent(_record(), b"png")

    async def upload(
        self,
        scope: RequestScope,
        session_id: str,
        upload_id: str,
        data: bytes,
        filename: str,
        media_type: str,
    ) -> ArtifactRecord:
        self.upload_calls.append(
            (scope, session_id, upload_id, data, filename, media_type)
        )
        return _record(filename=filename, size_bytes=len(data))

    async def list(
        self,
        scope: RequestScope,
        session_id: str,
        cursor: str | None,
        limit: int,
    ) -> ArtifactPage:
        self.list_calls.append((scope, session_id, cursor, limit))
        return ArtifactPage((_record(),), "cursor_2")

    async def read(
        self,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent:
        self.read_calls.append((scope, session_id, artifact_id))
        return self.read_content

    async def delete(
        self,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
        deletion_id: str,
    ) -> None:
        self.delete_calls.append(
            (scope, session_id, artifact_id, deletion_id)
        )


class _HostFactory:
    def __init__(self, *, max_size_bytes: int = 4) -> None:
        self.scope = RequestScope("tenant_authoritative", "cli_service")
        self.scope_resolver = _Resolver(self.scope)
        self.artifacts = _Artifacts(max_size_bytes=max_size_bytes)
        self.runs = object()
        self.commands = object()
        self.queries = object()
        self.events = object()
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


def test_upload_reads_only_policy_limit_plus_one_and_uses_basename(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        upload = tmp_path / "nested" / "drawing.png"
        upload.parent.mkdir()
        upload.write_bytes(b"png")
        factory = _HostFactory()
        stdout = StringIO()

        await run_artifact_command(
            _args(
                "upload",
                upload_id="upload_1",
                file=str(upload),
                media_type="image/png",
            ),
            factory,  # type: ignore[arg-type]
            stdout=stdout,
            binary_stdout=BytesIO(),
        )

        assert factory.scope_resolver.calls == [
            ("artifact_upload", CliResource("session_1"), "tenant_hint")
        ]
        assert factory.artifacts.upload_calls == [
            (
                factory.scope,
                "session_1",
                "upload_1",
                b"png",
                "drawing.png",
                "image/png",
            )
        ]
        assert json.loads(stdout.getvalue()) == {
            "created_at": "2026-07-21T12:30:00.000000Z",
            "filename": "drawing.png",
            "id": ARTIFACT_ID,
            "media_type": "image/png",
            "session_id": "session_1",
            "size_bytes": 3,
        }
        assert factory.entries == factory.exits == 1

    asyncio.run(scenario())


def test_upload_rejects_oversize_file_without_unbounded_read_or_service_call(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        upload = tmp_path / "large.bin"
        upload.write_bytes(b"12345" + b"unread tail")
        factory = _HostFactory(max_size_bytes=4)

        with pytest.raises(ArtifactTooLargeError):
            await run_artifact_command(
                _args(
                    "upload",
                    upload_id="upload_1",
                    file=str(upload),
                    media_type="image/png",
                ),
                factory,  # type: ignore[arg-type]
                stdout=StringIO(),
                binary_stdout=BytesIO(),
            )

        assert factory.artifacts.upload_calls == []
        assert factory.exits == 1

    asyncio.run(scenario())


def test_list_writes_allowlisted_page_and_applies_default_limit() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        stdout = StringIO()

        await run_artifact_command(
            _args("list", cursor=None, limit=None),
            factory,  # type: ignore[arg-type]
            stdout=stdout,
            binary_stdout=BytesIO(),
        )

        assert factory.scope_resolver.calls == [
            ("artifact_list", CliResource("session_1"), "tenant_hint")
        ]
        assert factory.artifacts.list_calls == [
            (factory.scope, "session_1", None, 20)
        ]
        payload = json.loads(stdout.getvalue())
        assert payload["next_cursor"] == "cursor_2"
        assert payload["items"][0]["id"] == ARTIFACT_ID
        assert "tenant_id" not in stdout.getvalue()
        assert "blob" not in stdout.getvalue()
        assert "path" not in stdout.getvalue()

    asyncio.run(scenario())


def test_read_dash_writes_only_raw_bytes_to_binary_stdout() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        stdout = StringIO()
        binary_stdout = BytesIO()

        await run_artifact_command(
            _args("read", artifact_id=ARTIFACT_ID, output="-"),
            factory,  # type: ignore[arg-type]
            stdout=stdout,
            binary_stdout=binary_stdout,
        )

        assert factory.scope_resolver.calls == [
            (
                "artifact_read",
                CliResource("session_1", artifact_id=ARTIFACT_ID),
                "tenant_hint",
            )
        ]
        assert factory.artifacts.read_calls == [
            (factory.scope, "session_1", ARTIFACT_ID)
        ]
        assert binary_stdout.getvalue() == b"png"
        assert stdout.getvalue() == ""

    asyncio.run(scenario())


def test_read_path_atomically_replaces_destination_then_writes_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        destination = tmp_path / "result.bin"
        destination.write_bytes(b"old")
        factory = _HostFactory()
        stdout = StringIO()
        real_replace = os.replace
        calls: list[tuple[Path, Path]] = []

        def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            calls.append((Path(source), Path(target)))
            assert Path(source).parent == destination.parent
            assert Path(source).read_bytes() == b"png"
            real_replace(source, target)

        monkeypatch.setattr("agentos.cli.commands.artifact.os.replace", replace)

        await run_artifact_command(
            _args("read", artifact_id=ARTIFACT_ID, output=str(destination)),
            factory,  # type: ignore[arg-type]
            stdout=stdout,
            binary_stdout=BytesIO(),
        )

        assert len(calls) == 1
        assert calls[0][1] == destination
        assert destination.read_bytes() == b"png"
        assert json.loads(stdout.getvalue())["id"] == ARTIFACT_ID
        assert str(destination) not in stdout.getvalue()

    asyncio.run(scenario())


def test_read_path_removes_temporary_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        destination = tmp_path / "result.bin"
        destination.write_bytes(b"old")
        factory = _HostFactory()
        stdout = StringIO()
        temporary: list[Path] = []

        def fail(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            temporary.append(Path(source))
            raise OSError("replace failed")

        monkeypatch.setattr("agentos.cli.commands.artifact.os.replace", fail)

        with pytest.raises(OSError):
            await run_artifact_command(
                _args("read", artifact_id=ARTIFACT_ID, output=str(destination)),
                factory,  # type: ignore[arg-type]
                stdout=stdout,
                binary_stdout=BytesIO(),
            )

        assert destination.read_bytes() == b"old"
        assert temporary and not temporary[0].exists()
        assert stdout.getvalue() == ""

    asyncio.run(scenario())


def test_delete_writes_stable_idempotent_receipt() -> None:
    async def scenario() -> None:
        factory = _HostFactory()
        stdout = StringIO()

        await run_artifact_command(
            _args(
                "delete",
                artifact_id=ARTIFACT_ID,
                deletion_id="deletion_1",
            ),
            factory,  # type: ignore[arg-type]
            stdout=stdout,
            binary_stdout=BytesIO(),
        )

        assert factory.scope_resolver.calls == [
            (
                "artifact_delete",
                CliResource("session_1", artifact_id=ARTIFACT_ID),
                "tenant_hint",
            )
        ]
        assert factory.artifacts.delete_calls == [
            (factory.scope, "session_1", ARTIFACT_ID, "deletion_1")
        ]
        assert json.loads(stdout.getvalue()) == {
            "artifact_id": ARTIFACT_ID,
            "deleted": True,
            "deletion_id": "deletion_1",
            "session_id": "session_1",
        }

    asyncio.run(scenario())
