from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from importlib import import_module
from inspect import signature
import signal
import sys
from typing import Mapping, Protocol, cast

from agentos.cli.auth import CliScopeResolver
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.distributed.migrations.models import MigrationReport


class CliServiceHost(Protocol):
    """借用一次 CLI service command 所需的最窄应用服务集合。"""

    scope_resolver: CliScopeResolver
    runs: RunSubmissionService
    commands: RunCommandService
    queries: RunQueryService
    events: RunEventStream
    artifacts: ArtifactService


class CliServer(Protocol):
    """由 server host 管理生命周期的 ingress server。"""

    async def serve(self) -> None: ...


class CliServerHost(Protocol):
    """拥有一次 serve command 的 server 生命周期。"""

    server: CliServer


class CliWorker(Protocol):
    """由 worker host 管理生命周期的分布式 Worker。"""

    async def start(self) -> None: ...

    async def wait(self) -> None: ...

    async def drain(self, timeout: float) -> None: ...


class CliWorkerHost(Protocol):
    """拥有一次 worker command 的 Worker 生命周期。"""

    worker: CliWorker


class CliRelay(Protocol):
    """由 relay host 管理生命周期的 Outbox Relay。"""

    async def relay_once(self) -> int: ...


class CliRelayHost(Protocol):
    """拥有一次 relay command 的 Relay 生命周期。"""

    relay: CliRelay


class CliMigrationService(Protocol):
    """只暴露 schema check/apply 的迁移应用服务。"""

    async def check(self) -> MigrationReport: ...

    async def apply(self) -> MigrationReport: ...


class CliMigrationHost(Protocol):
    """拥有一次 migrate command 的 PostgreSQL 迁移资源。"""

    migrations: CliMigrationService


class CliHostFactory(Protocol):
    """无 I/O 组合根；各 async context 退出时关闭其独占资源。"""

    def open_service_host(
        self,
    ) -> AbstractAsyncContextManager[CliServiceHost]: ...

    def open_server_host(
        self,
    ) -> AbstractAsyncContextManager[CliServerHost]: ...

    def open_worker_host(
        self,
    ) -> AbstractAsyncContextManager[CliWorkerHost]: ...

    def open_relay_host(
        self,
    ) -> AbstractAsyncContextManager[CliRelayHost]: ...

    def open_migration_host(
        self,
    ) -> AbstractAsyncContextManager[CliMigrationHost]: ...


class CliFactoryConfigurationError(ValueError):
    """表示 CLI factory 配置缺失或不满足组合合同。"""

    code = "invalid_cli_factory"
    message = "CLI application factory is invalid"

    def __init__(self) -> None:
        super().__init__(self.message)


class CliInterruptedError(RuntimeError):
    """表示进程信号触发的 CLI 生命周期已完成安全清理。"""

    def __init__(self) -> None:
        super().__init__("CLI operation interrupted")


def load_cli_host_factory(
    explicit: str | None,
    environ: Mapping[str, str],
) -> CliHostFactory:
    """从显式参数或环境变量加载无 I/O 的 CLI host factory。"""

    spec = explicit if explicit is not None else environ.get("AGENTOS_CLI_FACTORY")
    module_name, separator, callable_name = (spec or "").partition(":")
    if (
        not separator
        or not module_name
        or not callable_name
        or ":" in callable_name
    ):
        raise CliFactoryConfigurationError()
    try:
        factory = getattr(import_module(module_name), callable_name)
        signature(factory).bind()
        host_factory = factory()
    except Exception:
        raise CliFactoryConfigurationError() from None
    required = (
        "open_service_host",
        "open_server_host",
        "open_worker_host",
        "open_relay_host",
        "open_migration_host",
    )
    if any(not callable(getattr(host_factory, name, None)) for name in required):
        raise CliFactoryConfigurationError()
    return cast(CliHostFactory, host_factory)


async def run_cli_command(
    args: object,
    host_factory: CliHostFactory,
) -> None:
    """通过最窄 host 生命周期分派一个已解析的 CLI 命令。"""

    from agentos.cli.commands.artifact import run_artifact_command
    from agentos.cli.commands.migrate import run_migrate
    from agentos.cli.commands.relay import run_relay_start
    from agentos.cli.commands.run import run_run_command
    from agentos.cli.commands.serve import run_serve
    from agentos.cli.commands.worker import run_worker_start

    command = getattr(args, "command", None)
    if command == "run":
        await run_run_command(
            args,
            host_factory,
            stdin=sys.stdin,
            stdout=sys.stdout,
        )
        return
    if command == "artifact":
        binary_stdout = getattr(sys.stdout, "buffer", None)
        if binary_stdout is None:
            raise RuntimeError("binary stdout is unavailable")
        await run_artifact_command(
            args,
            host_factory,
            stdout=sys.stdout,
            binary_stdout=binary_stdout,
        )
        return
    if command == "worker":
        timeout = getattr(args, "drain_timeout", None)
        await run_worker_start(
            host_factory,
            drain_timeout=30.0 if timeout is None else timeout,
            wait_for_signal=_wait_for_shutdown_signal,
        )
        return
    if command == "relay":
        interval = getattr(args, "idle_interval", None)
        await run_relay_start(
            host_factory,
            idle_interval=1.0 if interval is None else interval,
            wait_for_signal=_wait_for_shutdown_signal,
        )
        return
    if command == "serve":
        await run_serve(host_factory)
        return
    if command == "migrate":
        await run_migrate(
            host_factory,
            check=getattr(args, "check", False),
            stdout=sys.stdout,
        )
        return
    raise ValueError("CLI command is invalid")


async def _wait_for_shutdown_signal() -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    previous: list[tuple[signal.Signals, object]] = []

    def stop(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(stopped.set)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous.append((signum, signal.getsignal(signum)))
            signal.signal(signum, stop)
        await stopped.wait()
    finally:
        for signum, handler in previous:
            signal.signal(signum, handler)


__all__ = [
    "CliFactoryConfigurationError",
    "CliHostFactory",
    "CliMigrationHost",
    "CliRelayHost",
    "CliServerHost",
    "CliServiceHost",
    "CliWorkerHost",
    "load_cli_host_factory",
    "run_cli_command",
]
