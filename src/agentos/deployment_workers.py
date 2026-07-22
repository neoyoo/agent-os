from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from threading import RLock
from typing import Protocol

from agentos.deployment_constants import WorkerProcessStatus
from agentos.deployment_types import WorkerProcessSpec, WorkerProcessState


class WorkerProcessSupervisor(Protocol):
    """Boundary for local worker process lifecycle evidence."""

    def start(self, spec: WorkerProcessSpec) -> WorkerProcessState:
        """Start one worker process and return its running state."""

    def stop(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Request a worker process stop and return final or current state."""

    def wait(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Wait for a worker process exit and return final or current state."""

    def heartbeat(
        self,
        worker_id: str,
        *,
        now: float | None = None,
    ) -> WorkerProcessState:
        """Record a worker heartbeat evidence timestamp."""

    def state(self, worker_id: str) -> WorkerProcessState:
        """Return the latest known state for one worker."""

    def is_running(self, worker_id: str) -> bool:
        """Return whether the worker process is currently alive."""

    def evidence(self, worker_id: str) -> dict[str, object]:
        """Return JSON-safe lifecycle evidence for one worker."""


class LocalSubprocessWorkerSupervisor:
    """Reference subprocess-backed worker supervisor for local deployments."""

    def __init__(self, *, clock: object | None = None) -> None:
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._states: dict[str, WorkerProcessState] = {}

    def start(self, spec: WorkerProcessSpec) -> WorkerProcessState:
        """Start one local subprocess without shell parsing."""

        with self._lock:
            self._reject_duplicate_running_worker(spec.worker_id)
            started_at = float(self._clock())
            try:
                process = subprocess.Popen(
                    spec.command,
                    cwd=spec.cwd,
                    env=self._process_env(spec),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError as exc:
                state = WorkerProcessState.from_spec(
                    spec,
                    status="failed",
                    started_at=started_at,
                    stopped_at=float(self._clock()),
                    error=str(exc) or exc.__class__.__name__,
                )
                self._states[spec.worker_id] = state
                raise
            state = WorkerProcessState.from_spec(
                spec,
                status="running",
                pid=process.pid,
                started_at=started_at,
            )
            self._processes[spec.worker_id] = process
            self._states[spec.worker_id] = state
            return state

    def stop(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Terminate one local subprocess and record stop evidence."""

        with self._lock:
            process = self._processes.get(worker_id)
            state = self._require_state(worker_id)
            if process is None or process.poll() is not None:
                return self._refresh_locked(worker_id)
            now = float(self._clock())
            state = replace(
                state,
                status="stopping",
                stop_requested_at=state.stop_requested_at or now,
            )
            self._states[worker_id] = state
            process.terminate()
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                return self._refresh_locked(worker_id)
        with self._lock:
            return self._refresh_locked(worker_id)

    def wait(
        self,
        worker_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkerProcessState:
        """Wait for one local subprocess to exit."""

        with self._lock:
            process = self._processes.get(worker_id)
            self._require_state(worker_id)
            if process is None or process.poll() is not None:
                return self._refresh_locked(worker_id)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                return self._refresh_locked(worker_id)
        with self._lock:
            return self._refresh_locked(worker_id)

    def state(self, worker_id: str) -> WorkerProcessState:
        """Return the latest known state for one worker."""

        with self._lock:
            return self._refresh_locked(worker_id)

    def heartbeat(
        self,
        worker_id: str,
        *,
        now: float | None = None,
    ) -> WorkerProcessState:
        """Record a JSON-safe heartbeat evidence timestamp."""

        with self._lock:
            state = self._refresh_locked(worker_id)
            if state.status not in {"running", "stopping"}:
                return state
            updated = replace(
                state,
                last_heartbeat_at=float(self._clock() if now is None else now),
            )
            self._states[worker_id] = updated
            return updated

    def is_running(self, worker_id: str) -> bool:
        """Return whether the worker process is currently alive."""

        with self._lock:
            process = self._processes.get(worker_id)
            return process is not None and process.poll() is None

    def evidence(self, worker_id: str) -> dict[str, object]:
        """Return JSON-safe lifecycle evidence for one worker."""

        return self.state(worker_id).to_evidence()

    def _reject_duplicate_running_worker(self, worker_id: str) -> None:
        process = self._processes.get(worker_id)
        if process is not None and process.poll() is None:
            raise ValueError("worker is already running")

    def _process_env(self, spec: WorkerProcessSpec) -> dict[str, str] | None:
        if not spec.env:
            return {}
        return {key: str(value) for key, value in spec.env.items()}

    def _refresh_locked(self, worker_id: str) -> WorkerProcessState:
        state = self._require_state(worker_id)
        process = self._processes.get(worker_id)
        if process is None:
            return state
        exit_code = process.poll()
        if exit_code is None:
            return state
        if state.stopped_at is not None and state.exit_code == exit_code:
            return state
        if state.stop_requested_at is not None:
            status: WorkerProcessStatus = "stopped"
        elif exit_code == 0:
            status = "exited"
        else:
            status = "failed"
        updated = replace(
            state,
            status=status,
            stopped_at=state.stopped_at or float(self._clock()),
            exit_code=exit_code,
        )
        self._states[worker_id] = updated
        return updated

    def _require_state(self, worker_id: str) -> WorkerProcessState:
        try:
            return self._states[worker_id]
        except KeyError as exc:
            raise KeyError(f"unknown worker process: {worker_id}") from exc


__all__ = [
    "LocalSubprocessWorkerSupervisor",
    "WorkerProcessSupervisor",
]
