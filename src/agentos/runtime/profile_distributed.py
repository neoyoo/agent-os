from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(slots=True)
class DistributedTeamRuntimeProfile:
    """面向分布式 Team Agent 的组合 Profile。"""

    store: object
    message_queue: object
    worker_session_provider: object
    worker_agent_provider: object | None = None
    retry_policy: object | None = None
    retry_store: object | None = None
    cancellation_store: object | None = None
    ui_stream: object | None = None
    notice_store: object | None = None
    wakeup_trigger: object | None = None
    daemon_team_id: str | None = None
    daemon_poll_interval_seconds: float = 0.5
    clock: object | None = None
    id_factory: object | None = None
    readiness: Mapping[str, object] = field(default_factory=dict)
    name: str = "distributed-team"
    team_runtime: object = field(init=False)
    worker_runner: object | None = field(init=False, default=None)
    worker_daemon: object | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """组装 Team Runtime 和可选的 Worker Host 边界。"""

        from agentos.multi import (
            TeamRuntime,
            TeamWorkerDaemon,
            TeamWorkerRunner,
        )

        self.team_runtime = TeamRuntime(
            store=self.store,
            message_queue=self.message_queue,
            notice_store=self.notice_store,
            wakeup_trigger=self.wakeup_trigger,
            worker_session_provider=self.worker_session_provider,
            ui_stream=self.ui_stream,
            clock=self.clock,
            id_factory=self.id_factory,
        )
        if self.worker_agent_provider is None:
            return
        self.worker_runner = TeamWorkerRunner(
            session_provider=self.worker_session_provider,
            agent_provider=self.worker_agent_provider,
            message_queue=self.message_queue,
            retry_policy=self.retry_policy,
            retry_store=self.retry_store,
            cancellation_store=self.cancellation_store,
            ui_stream=self.ui_stream,
            clock=self.clock,
        )
        self.worker_daemon = TeamWorkerDaemon(
            runner=self.worker_runner,
            team_id=self.daemon_team_id,
            poll_interval_seconds=self.daemon_poll_interval_seconds,
            clock=self.clock,
        )

    def build_team_runtime(self) -> object:
        """返回已组装的 TeamRuntime。"""

        return self.team_runtime

    def build_team_tools(self, *, owner_agent_id: str) -> object:
        """返回某个 Owner Agent 的 LLM 可调用 Team Tools。"""

        from agentos.multi import TeamTools

        return TeamTools(
            runtime=self.team_runtime,
            owner_agent_id=owner_agent_id,
        )

    def build_worker_runner(self) -> object:
        """返回已组装的 TeamWorkerRunner。"""

        if self.worker_runner is None:
            raise NotImplementedError(
                "DistributedTeamRuntimeProfile requires worker_agent_provider "
                "to build worker runner",
            )
        return self.worker_runner

    def build_worker_daemon(self) -> object:
        """返回已组装的 TeamWorkerDaemon。"""

        if self.worker_daemon is None:
            raise NotImplementedError(
                "DistributedTeamRuntimeProfile requires worker_agent_provider "
                "to build worker daemon",
            )
        return self.worker_daemon

    def readiness_checks(self) -> dict[str, object]:
        """返回 Profile 级 readiness checks。"""

        checks = {
            "profile": self.name,
            "team_runtime": self.team_runtime.__class__.__name__,
            "team_store": self.store.__class__.__name__,
            "message_queue": self.message_queue.__class__.__name__,
            "worker_session_provider": (
                self.worker_session_provider.__class__.__name__
            ),
            "worker_runner": self._adapter_name(self.worker_runner),
            "worker_daemon": self._adapter_name(self.worker_daemon),
            "ui_stream": self._adapter_name(self.ui_stream),
            "retry_store": self._adapter_name(self.retry_store),
            "cancellation_store": self._adapter_name(self.cancellation_store),
        }
        checks.update(dict(self.readiness))
        return checks

    def readiness_metadata(self) -> dict[str, object]:
        """返回分布式 Team Profile 的生产就绪元数据。"""

        return {
            "profile": self.name,
            "team_runtime": self.team_runtime.__class__.__name__,
            "team_store": self.store.__class__.__name__,
            "message_queue": self.message_queue.__class__.__name__,
            "worker_session_provider": (
                self.worker_session_provider.__class__.__name__
            ),
            "worker_agent_provider": self._adapter_name(
                self.worker_agent_provider,
            ),
            "worker_runner": self._adapter_name(self.worker_runner),
            "worker_daemon": self._adapter_name(self.worker_daemon),
            "retry_policy": self._adapter_name(self.retry_policy),
            "retry_store": self._adapter_name(self.retry_store),
            "cancellation_store": self._adapter_name(self.cancellation_store),
            "ui_stream": self._adapter_name(self.ui_stream),
            "daemon_team_id": self.daemon_team_id,
            "daemon_poll_interval_seconds": self.daemon_poll_interval_seconds,
            "production_gaps": [
                "credentials and migration rollout",
                "process supervision",
                "worker scaling policy",
                "OS/container sandboxing",
                "live backend verification",
            ],
        }

    @staticmethod
    def _adapter_name(adapter: object | None) -> str | None:
        return _adapter_name(adapter)


def _adapter_name(adapter: object | None) -> str | None:
    if adapter is None:
        return None
    return adapter.__class__.__name__


@dataclass(slots=True)
class DistributedAgentProfile:
    """分布式任务协调 Profile，不代表自动 Team Worker Session Runtime。"""

    coordinator: object
    resolver: object | None = None
    registry: object | None = None
    remote_task_executor: object | None = None
    readiness: Mapping[str, object] = field(default_factory=dict)
    name: str = "distributed-agent"

    def readiness_checks(self) -> dict[str, object]:
        """返回配置的 readiness checks。"""

        return dict(self.readiness)
