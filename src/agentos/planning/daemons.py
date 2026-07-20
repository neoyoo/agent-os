from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Literal

from agentos.planning.models import PLAN_STATUSES, PlanStatus
from agentos.planning.runtime import PlannerRuntime
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanSchedulerTickReport,
)


PlannerSchedulerDaemonStatus = Literal["idle", "running", "stopping", "stopped"]
PlannerClaimedSchedulerDaemonStatus = Literal[
    "idle",
    "running",
    "stopping",
    "stopped",
]


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonError:
    """Failure recorded while ticking one planner plan."""

    plan_id: str
    error: str


@dataclass(frozen=True, slots=True)
class PlannerSchedulerDaemonState:
    """Snapshot of a planner scheduler daemon lifecycle."""

    status: PlannerSchedulerDaemonStatus
    plan_ids: tuple[str, ...]
    poll_interval_seconds: float
    default_template_id: str | None = None
    retry_limit: int | None = None
    dispatch_limit: int | None = None
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_reports: tuple[PlanSchedulerTickReport, ...] = ()
    errors: tuple[PlannerSchedulerDaemonError, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerClaimedSchedulerDaemonError:
    """Failure recorded while running a claimed scheduler tick."""

    error: str


@dataclass(frozen=True, slots=True)
class PlannerClaimedSchedulerDaemonState:
    """Snapshot of a claimed scheduler daemon lifecycle."""

    status: PlannerClaimedSchedulerDaemonStatus
    worker_id: str
    lease_seconds: float
    owner_agent_id: str | None = None
    statuses: tuple[PlanStatus, ...] = ("draft", "running")
    limit: int | None = None
    default_template_id: str | None = None
    retry_limit: int | None = None
    dispatch_limit: int | None = None
    release_after_tick: bool = False
    poll_interval_seconds: float = 1.0
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_report: PlanClaimedSchedulerTickReport | None = None
    errors: tuple[PlannerClaimedSchedulerDaemonError, ...] = ()


class PlannerSchedulerDaemon:
    """Host loop that repeatedly runs bounded planner scheduler ticks."""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        plan_ids: tuple[str, ...],
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        poll_interval_seconds: float = 1.0,
        clock: object | None = None,
    ) -> None:
        self._validate_configuration(
            plan_ids=plan_ids,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.runtime = runtime
        self.plan_ids = tuple(plan_ids)
        self.default_template_id = default_template_id
        self.retry_limit = retry_limit
        self.dispatch_limit = dispatch_limit
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock if callable(clock) else time.time
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state = PlannerSchedulerDaemonState(
            status="idle",
            plan_ids=self.plan_ids,
            poll_interval_seconds=poll_interval_seconds,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
        )

    async def run_once(self) -> tuple[PlanSchedulerTickReport, ...]:
        """Run one daemon iteration without starting a background thread."""

        reports: list[PlanSchedulerTickReport] = []
        errors: list[PlannerSchedulerDaemonError] = []
        for plan_id in self.plan_ids:
            try:
                reports.append(
                    await self.runtime.scheduler_tick(
                        plan_id,
                        default_template_id=self.default_template_id,
                        retry_limit=self.retry_limit,
                        dispatch_limit=self.dispatch_limit,
                    ),
                )
            except Exception as error:
                errors.append(
                    PlannerSchedulerDaemonError(
                        plan_id=plan_id,
                        error=str(error) or error.__class__.__name__,
                    ),
                )
        self._record_run(tuple(reports), tuple(errors))
        return tuple(reports)

    async def start(self) -> None:
        """Start the background polling loop if it is not already running."""

        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._state = replace(
            self._state,
            status="running",
            started_at=float(self._clock()),
            stopped_at=None,
        )
        self._task = asyncio.create_task(
            self._run_loop(),
            name="agentos-planner-scheduler-daemon",
        )

    async def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        if self._state.status == "running":
            self._state = replace(self._state, status="stopping")

    async def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        task = self._task
        if task is None:
            return True
        done, _ = await asyncio.wait((task,), timeout=timeout)
        if task not in done:
            return False
        task.result()
        return True

    def is_running(self) -> bool:
        """Return whether the daemon task is currently alive."""

        task = self._task
        return task is not None and not task.done()

    def state(self) -> PlannerSchedulerDaemonState:
        """Return an immutable daemon state snapshot."""

        return self._state

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                await _wait_for_stop(
                    self._stop_event,
                    self.poll_interval_seconds,
                )
        finally:
            self._state = replace(
                self._state,
                status="stopped",
                stopped_at=float(self._clock()),
            )

    def _record_run(
        self,
        reports: tuple[PlanSchedulerTickReport, ...],
        errors: tuple[PlannerSchedulerDaemonError, ...],
    ) -> None:
        self._state = replace(
            self._state,
            iterations=self._state.iterations + 1,
            last_run_at=float(self._clock()),
            last_reports=reports,
            errors=errors,
        )

    def _validate_configuration(
        self,
        *,
        plan_ids: tuple[str, ...],
        retry_limit: int | None,
        dispatch_limit: int | None,
        poll_interval_seconds: float,
    ) -> None:
        if not plan_ids:
            raise ValueError("plan_ids must not be empty")
        if any(not plan_id.strip() for plan_id in plan_ids):
            raise ValueError("plan_ids must not contain empty ids")
        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")


class PlannerClaimedSchedulerDaemon:
    """Host loop that repeatedly runs claimed planner scheduler ticks."""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        release_after_tick: bool = False,
        poll_interval_seconds: float = 1.0,
        clock: object | None = None,
    ) -> None:
        self._validate_configuration(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            statuses=statuses,
            limit=limit,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.runtime = runtime
        self.worker_id = worker_id
        self.lease_seconds = float(lease_seconds)
        self.owner_agent_id = owner_agent_id
        self.statuses = tuple(statuses)
        self.limit = limit
        self.default_template_id = default_template_id
        self.retry_limit = retry_limit
        self.dispatch_limit = dispatch_limit
        self.release_after_tick = release_after_tick
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock if callable(clock) else time.time
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._state = PlannerClaimedSchedulerDaemonState(
            status="idle",
            worker_id=worker_id,
            lease_seconds=self.lease_seconds,
            owner_agent_id=owner_agent_id,
            statuses=self.statuses,
            limit=limit,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            release_after_tick=release_after_tick,
            poll_interval_seconds=poll_interval_seconds,
        )

    async def run_once(self) -> PlanClaimedSchedulerTickReport:
        """Run one claim-before-tick daemon iteration."""

        try:
            report = await self.runtime.claimed_scheduler_tick(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                owner_agent_id=self.owner_agent_id,
                statuses=self.statuses,
                limit=self.limit,
                default_template_id=self.default_template_id,
                retry_limit=self.retry_limit,
                dispatch_limit=self.dispatch_limit,
                release_after_tick=self.release_after_tick,
            )
        except Exception as error:
            self._record_error(
                PlannerClaimedSchedulerDaemonError(
                    error=str(error) or error.__class__.__name__,
                ),
            )
            raise
        self._record_success(report)
        return report

    async def start(self) -> None:
        """Start the background claim-before-tick polling loop."""

        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._state = replace(
            self._state,
            status="running",
            started_at=float(self._clock()),
            stopped_at=None,
        )
        self._task = asyncio.create_task(
            self._run_loop(),
            name="agentos-planner-claimed-scheduler-daemon",
        )

    async def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        if self._state.status == "running":
            self._state = replace(self._state, status="stopping")

    async def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        task = self._task
        if task is None:
            return True
        done, _ = await asyncio.wait((task,), timeout=timeout)
        if task not in done:
            return False
        task.result()
        return True

    def is_running(self) -> bool:
        """Return whether the daemon task is currently alive."""

        task = self._task
        return task is not None and not task.done()

    def state(self) -> PlannerClaimedSchedulerDaemonState:
        """Return an immutable daemon state snapshot."""

        return self._state

    async def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                await _wait_for_stop(
                    self._stop_event,
                    self.poll_interval_seconds,
                )
        finally:
            self._state = replace(
                self._state,
                status="stopped",
                stopped_at=float(self._clock()),
            )

    def _record_success(self, report: PlanClaimedSchedulerTickReport) -> None:
        self._state = replace(
            self._state,
            iterations=self._state.iterations + 1,
            last_run_at=float(self._clock()),
            last_report=report,
            errors=(),
        )

    def _record_error(self, error: PlannerClaimedSchedulerDaemonError) -> None:
        self._state = replace(
            self._state,
            iterations=self._state.iterations + 1,
            last_run_at=float(self._clock()),
            last_report=None,
            errors=(error,),
        )

    def _validate_configuration(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        statuses: tuple[PlanStatus, ...],
        limit: int | None,
        retry_limit: int | None,
        dispatch_limit: int | None,
        poll_interval_seconds: float,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if float(lease_seconds) <= 0:
            raise ValueError("lease_seconds must be > 0")
        _validate_plan_status_tuple(statuses)
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")
        if retry_limit is not None and retry_limit < 1:
            raise ValueError("retry_limit must be >= 1")
        if dispatch_limit is not None and dispatch_limit < 1:
            raise ValueError("dispatch_limit must be >= 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")


def _validate_plan_status_tuple(
    statuses: tuple[PlanStatus, ...],
) -> tuple[PlanStatus, ...]:
    if not statuses:
        raise ValueError("statuses must not be empty")
    for status in statuses:
        if status not in PLAN_STATUSES:
            raise ValueError(f"unsupported plan status in statuses: {status}")
    return tuple(statuses)


async def _wait_for_stop(event: asyncio.Event, timeout: float) -> None:
    if event.is_set():
        return
    if timeout == 0:
        await asyncio.sleep(0)
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        pass


__all__ = [
    "PlannerClaimedSchedulerDaemon",
    "PlannerClaimedSchedulerDaemonError",
    "PlannerClaimedSchedulerDaemonState",
    "PlannerClaimedSchedulerDaemonStatus",
    "PlannerSchedulerDaemon",
    "PlannerSchedulerDaemonError",
    "PlannerSchedulerDaemonState",
    "PlannerSchedulerDaemonStatus",
]
