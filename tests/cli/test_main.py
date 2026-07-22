from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from agentos.cli import main
from agentos.distributed.migrations.models import MigrationReport


class _MigrationService:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    async def check(self) -> MigrationReport:
        if self.error is not None:
            raise self.error
        return MigrationReport(2, 2, (), False)

    async def apply(self) -> MigrationReport:
        return MigrationReport(2, 2, (), False)


class _Factory:
    def __init__(self, error: BaseException | None = None) -> None:
        self.migrations = _MigrationService(error)
        self.opened: list[str] = []

    @asynccontextmanager
    async def open_migration_host(self):  # type: ignore[no-untyped-def]
        self.opened.append("migration")
        yield self


class _BombFactory:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"init must not access factory: {name}")


class _FalseyFactory(_Factory):
    def __bool__(self) -> bool:
        return False


class _Worker:
    def __init__(self) -> None:
        self.drain_calls: list[float] = []

    async def start(self) -> None:
        return None

    async def wait(self) -> None:
        await asyncio.Event().wait()

    async def drain(self, timeout: float) -> None:
        self.drain_calls.append(timeout)


class _Relay:
    async def relay_once(self) -> int:
        return 0


class _LifecycleFactory:
    def __init__(self) -> None:
        self.worker = _Worker()
        self.relay = _Relay()
        self.closed: list[str] = []

    @asynccontextmanager
    async def open_worker_host(self):  # type: ignore[no-untyped-def]
        try:
            yield self
        finally:
            self.closed.append("worker")

    @asynccontextmanager
    async def open_relay_host(self):  # type: ignore[no-untyped-def]
        try:
            yield self
        finally:
            self.closed.append("relay")


def test_init_does_not_load_or_access_factory(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        ["init", str(tmp_path / "demo")],
        host_factory=_BombFactory(),  # type: ignore[arg-type]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == '{"initialized":true}\n'


def test_migrate_uses_injected_narrow_host_factory(capsys) -> None:
    factory = _Factory()

    exit_code = main(
        ["migrate", "--check"],
        host_factory=factory,  # type: ignore[arg-type]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory.opened == ["migration"]
    assert captured.err == ""
    assert captured.out == (
        '{"applied_versions":[],"changed":false,'
        '"current_version":2,"target_version":2}\n'
    )


def test_falsey_injected_factory_still_replaces_factory_loading(capsys) -> None:
    factory = _FalseyFactory()

    exit_code = main(
        ["migrate", "--check"],
        host_factory=factory,  # type: ignore[arg-type]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert factory.opened == ["migration"]
    assert captured.err == ""


def test_missing_factory_is_usage_error_without_backend_fallback(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("AGENTOS_CLI_FACTORY", raising=False)

    exit_code = main(
        [
            "run",
            "get",
            "--tenant",
            "tenant_1",
            "--session-id",
            "session_1",
            "--run-id",
            "run_1",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == (
        '{"code":"invalid_cli_factory",'
        '"message":"CLI application factory is invalid"}\n'
    )


def test_internal_failure_is_redacted(capsys) -> None:
    secret = "postgresql://user:password token=secret SELECT C:/private"

    exit_code = main(
        ["migrate", "--check"],
        host_factory=_Factory(RuntimeError(secret)),  # type: ignore[arg-type]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == '{"code":"internal_error","message":"internal error"}\n'
    assert secret not in captured.err


def test_usage_error_is_single_line_json_and_redacts_unknown_value(capsys) -> None:
    secret = "postgresql://user:password@host/database"

    exit_code = main(
        ["migrate", "--dsn", secret],
        host_factory=_BombFactory(),  # type: ignore[arg-type]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == (
        '{"code":"invalid_cli_input","message":"invalid CLI input"}\n'
    )
    assert secret not in captured.err


@pytest.mark.parametrize("command", ["worker", "relay"])
def test_signal_shutdown_returns_interrupted_after_host_cleanup(
    command: str,
    monkeypatch,
    capsys,
) -> None:
    async def signal_received() -> None:
        return None

    monkeypatch.setattr(
        "agentos.cli.application._wait_for_shutdown_signal",
        signal_received,
    )
    factory = _LifecycleFactory()

    exit_code = main(
        [command, "start"],
        host_factory=factory,  # type: ignore[arg-type]
    )

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.out == ""
    assert captured.err == (
        '{"code":"interrupted","message":"operation interrupted"}\n'
    )
    assert factory.closed == [command]
    if command == "worker":
        assert factory.worker.drain_calls == [30.0]


def test_help_and_invalid_legacy_cli_exit_before_factory_import(capsys) -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--factory", "does_not_exist:create", "--help"])
    assert help_exit.value.code == 0

    legacy_exit = main(["run", "agentos_app:app"])

    captured = capsys.readouterr()
    assert legacy_exit == 2
    assert "does_not_exist" not in captured.err
    assert captured.err == (
        '{"code":"invalid_cli_input","message":"invalid CLI input"}\n'
    )
