from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Literal, Mapping, Protocol, cast
from uuid import uuid4

from agentos.capabilities import RegisteredTool, ToolRegistry
from agentos.multi.message_queue import AgentMessageQueue, QueueDelivery
from agentos.multi.types import AgentEnvelope
from agentos.runtime.agent import Agent
from agentos.runtime.run import LocalContinuationInput
from agentos.multi.team_notices import TeamNoticeStore
from agentos.workspace import WorkspaceHandle, WorkspacePolicy, WorkspacePolicyError


TeamStatus = Literal["active", "deleted"]
TeamMemberRole = Literal["leader", "worker"]
TeamMemberStatus = Literal["active", "offline", "removed"]
TeamMessageKind = Literal["instruction", "observation", "result", "notice"]
TeamWorkerSessionStatus = Literal["created", "closed"]
TeamWorkerDaemonStatus = Literal["idle", "running", "stopping", "stopped"]
TeamWorkerRetryStatus = Literal["scheduled", "exhausted"]
TeamWorkerCancellationStatus = Literal["requested", "acknowledged", "cleared"]
TeamWorkerResultStatus = Literal[
    "completed",
    "failed",
    "retry_skipped",
    "cancelled",
]
TeamUiEventKind = Literal[
    "team_created",
    "member_added",
    "message_appended",
    "worker_run_completed",
    "worker_run_failed",
    "worker_run_retry_skipped",
    "worker_run_cancelled",
    "team_deleted",
]


class TeamError(RuntimeError):
    """team runtime 基础错误。"""


class TeamNotFoundError(TeamError):
    """team 不存在或不可用。"""


class TeamMembershipError(TeamError):
    """agent 不是 team 成员或收件人无效。"""


class TeamWorkerPermissionError(TeamError, PermissionError):
    """team worker session permission policy rejected a request."""


class TeamToolAuthorizationError(TeamError, PermissionError):
    """Team tool authorization policy rejected an LLM-callable operation."""


class TeamToolAuthorizationPolicy(Protocol):
    """Authorization boundary for LLM-callable team tools."""

    def authorize_team_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        team_id: str | None,
    ) -> None:
        """Raise TeamToolAuthorizationError when the tool call is not allowed."""


class DefaultTeamToolAuthorizationPolicy:
    """Least-privilege team tool policy for production SDK defaults."""

    _management_tools: frozenset[str] = frozenset(
        {"team_create", "agent_create", "team_delete"},
    )

    def authorize_team_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        team_id: str | None,
    ) -> None:
        """Deny management tools unless deployment code injects a policy."""

        if tool_name in self._management_tools:
            raise TeamToolAuthorizationError(
                f"team tool requires explicit authorization: {tool_name}",
            )


class AllowAllTeamToolAuthorizationPolicy:
    """Local/dev policy that allows every team tool operation."""

    def authorize_team_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        team_id: str | None,
    ) -> None:
        """Allow all team tool calls."""


@dataclass(frozen=True, slots=True)
class TeamRecord:
    """team 的稳定声明。"""

    team_id: str
    name: str
    description: str
    leader_agent_id: str
    created_at: float
    status: TeamStatus = "active"
    workspace: WorkspaceHandle | None = None


@dataclass(frozen=True, slots=True)
class TeamMemberRecord:
    """team 成员声明。"""

    team_id: str
    agent_id: str
    role: TeamMemberRole
    session_id: str | None = None
    capabilities: tuple[str, ...] = ()
    workspace: WorkspaceHandle | None = None
    status: TeamMemberStatus = "active"
    created_at: float = 0


@dataclass(frozen=True, slots=True)
class TeamMessage:
    """team conversation 中的一条持久消息。"""

    message_id: str
    team_id: str
    from_agent_id: str
    content: str
    created_at: float
    to_agent_id: str | None = None
    kind: TeamMessageKind = "observation"
    correlation_id: str | None = None
    artifact_handles: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamUiEvent:
    """Stable event for UI projections of team activity."""

    event_id: int
    team_id: str
    kind: TeamUiEventKind
    payload: Mapping[str, object]
    created_at: float


class TeamUiStreamStore(Protocol):
    """Append-only UI event projection for a team."""

    def append(
        self,
        *,
        team_id: str,
        kind: TeamUiEventKind,
        payload: Mapping[str, object],
        created_at: float,
    ) -> TeamUiEvent:
        """Append and return a UI event."""

    def list_events(
        self,
        team_id: str,
        *,
        after_event_id: int | None = None,
    ) -> tuple[TeamUiEvent, ...]:
        """Return UI events after an optional cursor."""


class InMemoryTeamUiStreamStore:
    """Bounded in-memory team UI event stream for local tests/prototypes."""

    def __init__(self, max_events_per_team: int = 512) -> None:
        if max_events_per_team < 1:
            raise ValueError("max_events_per_team must be >= 1")
        self.max_events_per_team = max_events_per_team
        self._events: dict[str, deque[TeamUiEvent]] = defaultdict(deque)
        self._next_event_id: dict[str, int] = defaultdict(lambda: 1)
        self._lock = RLock()

    def append(
        self,
        *,
        team_id: str,
        kind: TeamUiEventKind,
        payload: Mapping[str, object],
        created_at: float,
    ) -> TeamUiEvent:
        with self._lock:
            event = TeamUiEvent(
                event_id=self._next_event_id[team_id],
                team_id=team_id,
                kind=kind,
                payload=dict(payload),
                created_at=created_at,
            )
            self._next_event_id[team_id] += 1
            events = self._events[team_id]
            events.append(event)
            while len(events) > self.max_events_per_team:
                events.popleft()
        return event

    def list_events(
        self,
        team_id: str,
        *,
        after_event_id: int | None = None,
    ) -> tuple[TeamUiEvent, ...]:
        with self._lock:
            events = tuple(self._events.get(team_id, ()))
        if after_event_id is None:
            return events
        return tuple(event for event in events if event.event_id > after_event_id)


@dataclass(frozen=True, slots=True)
class TeamWorkerSessionRequest:
    """Request to create a worker session for a team member."""

    team_id: str
    agent_id: str
    role: TeamMemberRole
    capabilities: tuple[str, ...]
    requested_session_id: str | None = None
    team_workspace: WorkspaceHandle | None = None
    requested_workspace: WorkspaceHandle | None = None
    created_by_agent_id: str | None = None
    created_at: float = 0


@dataclass(frozen=True, slots=True)
class TeamWorkerSession:
    """Stable lifecycle handle for a team worker session."""

    team_id: str
    agent_id: str
    session_id: str
    status: TeamWorkerSessionStatus = "created"
    workspace: WorkspaceHandle | None = None
    capabilities: tuple[str, ...] = ()
    created_at: float = 0
    closed_at: float | None = None


class TeamWorkerSessionProvider(Protocol):
    """Boundary for creating and closing independent team worker sessions."""

    def create_worker_session(
        self,
        request: TeamWorkerSessionRequest,
    ) -> TeamWorkerSession:
        """Create or register a worker session."""

    def close_worker_sessions(self, team_id: str, *, now: float) -> None:
        """Close worker sessions for one team."""

    def list_sessions(self, team_id: str | None = None) -> list[TeamWorkerSession]:
        """List known worker sessions."""


def _team_worker_workspace_policy() -> WorkspacePolicy:
    return WorkspacePolicy(
        allowed_scopes=frozenset(
            {"process", "agent", "user", "session", "team", "task"},
        ),
    )


@dataclass(frozen=True, slots=True)
class TeamWorkerPermissionPolicy:
    """Validate permission downgrade when a team worker session is created."""

    workspace_policy: WorkspacePolicy = field(
        default_factory=_team_worker_workspace_policy,
    )
    allowed_capabilities: frozenset[str] | None = None

    def resolve_workspace(
        self,
        request: TeamWorkerSessionRequest,
    ) -> WorkspaceHandle | None:
        """Return the worker workspace after enforcing narrowing rules."""

        self.ensure_capabilities(request.capabilities)
        if request.requested_workspace is None:
            return request.team_workspace
        if request.team_workspace is None:
            raise TeamWorkerPermissionError(
                "team workspace is required to validate requested worker workspace",
            )
        try:
            self.workspace_policy.ensure_child_workspace_allowed(
                request.team_workspace,
                request.requested_workspace,
            )
        except WorkspacePolicyError as exc:
            message = str(exc)
            if "escapes parent workspace root" in message:
                message = "requested worker workspace is outside team workspace"
            raise TeamWorkerPermissionError(message) from exc
        self._ensure_root_narrowed(
            request.team_workspace,
            request.requested_workspace,
        )
        return request.requested_workspace

    def ensure_capabilities(self, capabilities: tuple[str, ...]) -> None:
        if self.allowed_capabilities is None:
            return
        denied = sorted(set(capabilities).difference(self.allowed_capabilities))
        if denied:
            raise TeamWorkerPermissionError(
                f"capabilities not allowed: {', '.join(denied)}",
            )

    def _ensure_root_narrowed(
        self,
        team_workspace: WorkspaceHandle,
        worker_workspace: WorkspaceHandle,
    ) -> None:
        if team_workspace.root is None:
            if worker_workspace.root is not None:
                raise TeamWorkerPermissionError(
                    "cannot verify worker workspace root without team workspace root",
                )
            return
        if worker_workspace.root is None:
            raise TeamWorkerPermissionError(
                "worker workspace root is required when team workspace has a local root",
            )
        team_root = Path(team_workspace.root).resolve(strict=False)
        worker_root = Path(worker_workspace.root).resolve(strict=False)
        try:
            worker_root.relative_to(team_root)
        except ValueError as exc:
            raise TeamWorkerPermissionError(
                "worker workspace root is outside team workspace root",
            ) from exc


class InMemoryTeamWorkerSessionProvider:
    """In-memory worker session provider for tests and local prototypes."""

    def __init__(
        self,
        id_factory: object | None = None,
        permission_policy: TeamWorkerPermissionPolicy | None = None,
    ) -> None:
        self._sessions: dict[tuple[str, str], TeamWorkerSession] = {}
        self._id_factory = id_factory if callable(id_factory) else self._default_id
        self.permission_policy = permission_policy or TeamWorkerPermissionPolicy()
        self._lock = RLock()

    def create_worker_session(
        self,
        request: TeamWorkerSessionRequest,
    ) -> TeamWorkerSession:
        session_id = request.requested_session_id or str(self._id_factory(request))
        workspace = self.permission_policy.resolve_workspace(request)
        session = TeamWorkerSession(
            team_id=request.team_id,
            agent_id=request.agent_id,
            session_id=session_id,
            workspace=workspace,
            capabilities=tuple(request.capabilities),
            created_at=request.created_at,
        )
        with self._lock:
            self._sessions[(request.team_id, request.agent_id)] = session
        return session

    def close_worker_sessions(self, team_id: str, *, now: float) -> None:
        with self._lock:
            for key, session in list(self._sessions.items()):
                if key[0] != team_id or session.status == "closed":
                    continue
                self._sessions[key] = replace(
                    session,
                    status="closed",
                    closed_at=now,
                )

    def get_session(
        self,
        team_id: str,
        agent_id: str,
    ) -> TeamWorkerSession | None:
        with self._lock:
            return self._sessions.get((team_id, agent_id))

    def list_sessions(self, team_id: str | None = None) -> list[TeamWorkerSession]:
        with self._lock:
            sessions = list(self._sessions.values())
        if team_id is None:
            return sessions
        return [session for session in sessions if session.team_id == team_id]

    def _default_id(self, request: TeamWorkerSessionRequest) -> str:
        return f"team_worker_session_{uuid4().hex}"


class TeamWorkerAgentProvider(Protocol):
    """Resolve an executable agent for a team worker session."""

    def get_worker_agent(self, session: TeamWorkerSession) -> Agent:
        """返回 worker session 使用的异步 Agent。"""


@dataclass(frozen=True, slots=True)
class TeamWorkerRunError:
    """Failure recorded while running one worker delivery."""

    team_id: str
    agent_id: str
    session_id: str
    delivery_id: str
    error: str


@dataclass(frozen=True, slots=True)
class TeamWorkerRetryPolicy:
    """Retry/backoff policy for team worker continuation deliveries."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_multiplier: float = 1.0
    ack_exhausted: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return retry delay after the given failed attempt."""

        return self.backoff_seconds * (
            self.backoff_multiplier ** max(0, attempt - 1)
        )


@dataclass(frozen=True, slots=True)
class TeamWorkerRetryRecord:
    """Retry state for one failed team worker delivery."""

    team_id: str
    agent_id: str
    session_id: str
    delivery_id: str
    message_id: str | None
    attempts: int
    status: TeamWorkerRetryStatus
    next_run_at: float
    last_error: str
    delivery: QueueDelivery | None = None
    exhausted_at: float | None = None


class TeamWorkerRetryStore(Protocol):
    """Boundary for team worker retry/backoff state."""

    def get(self, agent_id: str, delivery_id: str) -> TeamWorkerRetryRecord | None:
        """Return retry record for one delivery."""

    def record_failure(self, record: TeamWorkerRetryRecord) -> None:
        """Store or replace a retry record."""

    def clear(self, agent_id: str, delivery_id: str) -> None:
        """Remove retry state for one delivery."""

    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerRetryRecord, ...]:
        """Return retry records."""


class InMemoryTeamWorkerRetryStore:
    """Thread-safe in-memory retry store for worker continuation deliveries."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], TeamWorkerRetryRecord] = {}
        self._lock = RLock()

    def get(self, agent_id: str, delivery_id: str) -> TeamWorkerRetryRecord | None:
        with self._lock:
            return self._records.get((agent_id, delivery_id))

    def record_failure(self, record: TeamWorkerRetryRecord) -> None:
        with self._lock:
            self._records[(record.agent_id, record.delivery_id)] = record

    def clear(self, agent_id: str, delivery_id: str) -> None:
        with self._lock:
            self._records.pop((agent_id, delivery_id), None)

    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerRetryRecord, ...]:
        with self._lock:
            records = tuple(self._records.values())
        if team_id is None:
            return records
        return tuple(record for record in records if record.team_id == team_id)


@dataclass(frozen=True, slots=True)
class TeamWorkerCancellationRecord:
    """Cancellation intent for team worker deliveries or worker sessions."""

    team_id: str
    agent_id: str
    session_id: str
    reason: str
    requested_at: float
    status: TeamWorkerCancellationStatus = "requested"
    delivery_id: str | None = None
    message_id: str | None = None
    acknowledged_at: float | None = None
    cleared_at: float | None = None


class TeamWorkerCancellationStore(Protocol):
    """Boundary for team worker cancellation intent state."""

    def request_cancel(self, record: TeamWorkerCancellationRecord) -> None:
        """Store or replace a cancellation intent."""

    def match(
        self,
        *,
        team_id: str,
        agent_id: str,
        session_id: str,
        delivery_id: str | None = None,
        message_id: str | None = None,
    ) -> TeamWorkerCancellationRecord | None:
        """Return the best active cancellation intent for a delivery."""

    def acknowledge(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        """Acknowledge an exact cancellation intent."""

    def clear(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        """Clear a cancellation intent."""

    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerCancellationRecord, ...]:
        """Return cancellation records."""


class InMemoryTeamWorkerCancellationStore:
    """Thread-safe in-memory cancellation store for team worker deliveries."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str | None, str | None], TeamWorkerCancellationRecord] = {}
        self._lock = RLock()

    def request_cancel(self, record: TeamWorkerCancellationRecord) -> None:
        with self._lock:
            self._records[self._key(record)] = record

    def match(
        self,
        *,
        team_id: str,
        agent_id: str,
        session_id: str,
        delivery_id: str | None = None,
        message_id: str | None = None,
    ) -> TeamWorkerCancellationRecord | None:
        with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if (
                    record.team_id == team_id
                    and record.agent_id == agent_id
                    and record.session_id == session_id
                    and record.status == "requested"
                    and (
                        (
                            record.delivery_id is not None
                            and record.delivery_id == delivery_id
                        )
                        or (
                            record.message_id is not None
                            and record.message_id == message_id
                        )
                        or (
                            record.delivery_id is None
                            and record.message_id is None
                        )
                    )
                )
            ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda record: (
                0
                if record.delivery_id is not None or record.message_id is not None
                else 1,
                record.requested_at,
            ),
        )[0]

    def acknowledge(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        updated = replace(
            record,
            status="acknowledged",
            acknowledged_at=now,
        )
        with self._lock:
            self._records[self._key(record)] = updated
        return updated

    def clear(
        self,
        record: TeamWorkerCancellationRecord,
        *,
        now: float,
    ) -> TeamWorkerCancellationRecord:
        updated = replace(record, status="cleared", cleared_at=now)
        with self._lock:
            self._records[self._key(record)] = updated
        return updated

    def list_records(
        self,
        team_id: str | None = None,
    ) -> tuple[TeamWorkerCancellationRecord, ...]:
        with self._lock:
            records = tuple(self._records.values())
        if team_id is None:
            return records
        return tuple(record for record in records if record.team_id == team_id)

    def _key(
        self,
        record: TeamWorkerCancellationRecord,
    ) -> tuple[str, str, str | None, str | None]:
        return (
            record.agent_id,
            record.session_id,
            record.delivery_id,
            record.message_id,
        )


@dataclass(frozen=True, slots=True)
class TeamWorkerRunResult:
    """Result of processing one worker delivery."""

    team_id: str
    agent_id: str
    session_id: str
    delivery_id: str
    status: TeamWorkerResultStatus
    message_id: str | None = None
    error: str | None = None
    retry_status: Literal["scheduled", "exhausted", "cleared"] | None = None
    attempt: int | None = None
    next_run_at: float | None = None
    cancellation_status: TeamWorkerCancellationStatus | None = None


class TeamWorkerRunner:
    """Run worker continuation turns for queued team messages."""

    def __init__(
        self,
        *,
        session_provider: TeamWorkerSessionProvider,
        agent_provider: TeamWorkerAgentProvider,
        message_queue: AgentMessageQueue,
        retry_policy: TeamWorkerRetryPolicy | None = None,
        retry_store: TeamWorkerRetryStore | None = None,
        cancellation_store: TeamWorkerCancellationStore | None = None,
        ui_stream: TeamUiStreamStore | None = None,
        clock: object | None = None,
    ) -> None:
        self.session_provider = session_provider
        self.agent_provider = agent_provider
        self.message_queue = message_queue
        self.retry_policy = retry_policy
        self.retry_store = retry_store or (
            InMemoryTeamWorkerRetryStore()
            if retry_policy is not None
            else None
        )
        self.cancellation_store = cancellation_store
        self.ui_stream = ui_stream
        self._clock = clock if callable(clock) else time.time
        self._errors: list[TeamWorkerRunError] = []

    async def run_pending(
        self,
        team_id: str | None = None,
    ) -> list[TeamWorkerRunResult]:
        """Process one current batch of team-message deliveries."""

        results: list[TeamWorkerRunResult] = []
        for session in self.session_provider.list_sessions(team_id):
            if session.status != "created":
                continue
            retry_results = await self._run_retry_records(session)
            results.extend(retry_results)
            for result in retry_results:
                self._publish_ui_result(result)
            for delivery in self._collect_team_deliveries(session):
                if self._retry_store() is not None:
                    existing = self._retry_store().get(
                        session.agent_id,
                        delivery.delivery_id,
                    )
                    if (
                        existing is not None
                        and existing.delivery is not None
                    ):
                        continue
                result = await self._run_delivery(session, delivery)
                if result is not None:
                    results.append(result)
                    self._publish_ui_result(result)
        return results

    def errors(self) -> tuple[TeamWorkerRunError, ...]:
        """Return recorded runner failures."""

        return tuple(self._errors)

    def retry_records(self) -> tuple[TeamWorkerRetryRecord, ...]:
        """Return current retry records."""

        store = self._retry_store()
        if store is None:
            return ()
        return store.list_records()

    def cancellation_records(self) -> tuple[TeamWorkerCancellationRecord, ...]:
        """Return current cancellation records."""

        store = self._cancellation_store()
        if store is None:
            return ()
        return store.list_records()

    async def _run_retry_records(
        self,
        session: TeamWorkerSession,
    ) -> list[TeamWorkerRunResult]:
        store = self._retry_store()
        if store is None:
            return []
        results: list[TeamWorkerRunResult] = []
        for record in store.list_records(session.team_id):
            if record.agent_id != session.agent_id:
                continue
            if record.status == "exhausted":
                continue
            cancellation = self._matching_cancellation(
                session,
                delivery_id=record.delivery_id,
                message_id=record.message_id,
            )
            if cancellation is not None:
                result = self._cancel_delivery(
                    session,
                    delivery=record.delivery,
                    message_id=record.message_id,
                    cancellation=cancellation,
                )
                store.clear(session.agent_id, record.delivery_id)
                results.append(result)
                continue
            if record.next_run_at > float(self._clock()):
                results.append(
                    TeamWorkerRunResult(
                        team_id=record.team_id,
                        agent_id=record.agent_id,
                        session_id=record.session_id,
                        delivery_id=record.delivery_id,
                        status="retry_skipped",
                        message_id=record.message_id,
                        error=record.last_error,
                        retry_status=record.status,
                        attempt=record.attempts,
                        next_run_at=record.next_run_at,
                    ),
                )
                continue
            if record.delivery is None:
                continue
            result = await self._run_delivery(
                session,
                record.delivery,
                prior_attempts=record.attempts,
            )
            if result is not None:
                results.append(result)
        return results

    async def _run_delivery(
        self,
        session: TeamWorkerSession,
        delivery: QueueDelivery,
        *,
        prior_attempts: int = 0,
    ) -> TeamWorkerRunResult | None:
        envelope = delivery.envelope
        if envelope.type != "team_message" or not isinstance(
            envelope.payload,
            TeamMessage,
        ):
            return None
        if not self._delivery_matches_session(session, delivery):
            return None
        cancellation = self._matching_cancellation(
            session,
            delivery_id=delivery.delivery_id,
            message_id=envelope.payload.message_id,
        )
        if cancellation is not None:
            return self._cancel_delivery(
                session,
                delivery=delivery,
                message_id=envelope.payload.message_id,
                cancellation=cancellation,
            )
        agent = self.agent_provider.get_worker_agent(session)
        try:
            await agent.run(LocalContinuationInput())
        except Exception as error:
            error_text = str(error)
            attempt = prior_attempts + 1
            self._errors.append(
                TeamWorkerRunError(
                    team_id=session.team_id,
                    agent_id=session.agent_id,
                    session_id=session.session_id,
                    delivery_id=delivery.delivery_id,
                    error=error_text,
                ),
            )
            retry_status: Literal["scheduled", "exhausted"] | None = None
            next_run_at: float | None = None
            policy = self.retry_policy
            store = self._retry_store()
            if policy is not None and store is not None:
                retry_status = (
                    "exhausted"
                    if attempt >= policy.max_attempts
                    else "scheduled"
                )
                now = float(self._clock())
                next_run_at = now if retry_status == "exhausted" else (
                    now + policy.delay_for_attempt(attempt)
                )
                store.record_failure(
                    TeamWorkerRetryRecord(
                        team_id=session.team_id,
                        agent_id=session.agent_id,
                        session_id=session.session_id,
                        delivery_id=delivery.delivery_id,
                        message_id=envelope.payload.message_id,
                        attempts=attempt,
                        status=retry_status,
                        next_run_at=next_run_at,
                        last_error=error_text,
                        delivery=delivery,
                        exhausted_at=(
                            now if retry_status == "exhausted" else None
                        ),
                    ),
                )
                if retry_status == "exhausted" and policy.ack_exhausted:
                    self.message_queue.ack(session.agent_id, delivery.delivery_id)
            return TeamWorkerRunResult(
                team_id=session.team_id,
                agent_id=session.agent_id,
                session_id=session.session_id,
                delivery_id=delivery.delivery_id,
                status="failed",
                message_id=envelope.payload.message_id,
                error=error_text,
                retry_status=retry_status,
                attempt=attempt,
                next_run_at=next_run_at,
            )
        store = self._retry_store()
        retry_status: Literal["cleared"] | None = None
        attempt: int | None = None
        if store is not None:
            existing = store.get(session.agent_id, delivery.delivery_id)
            if existing is not None:
                attempt = existing.attempts + 1
                retry_status = "cleared"
                store.clear(session.agent_id, delivery.delivery_id)
        self.message_queue.ack(session.agent_id, delivery.delivery_id)
        return TeamWorkerRunResult(
            team_id=session.team_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            delivery_id=delivery.delivery_id,
            status="completed",
            message_id=envelope.payload.message_id,
            retry_status=retry_status,
            attempt=attempt,
        )

    def _retry_store(self) -> TeamWorkerRetryStore | None:
        return self.retry_store

    def _cancellation_store(self) -> TeamWorkerCancellationStore | None:
        return self.cancellation_store

    def _publish_ui_result(self, result: TeamWorkerRunResult) -> None:
        if self.ui_stream is None:
            return
        kind_by_status: dict[TeamWorkerResultStatus, TeamUiEventKind] = {
            "completed": "worker_run_completed",
            "failed": "worker_run_failed",
            "retry_skipped": "worker_run_retry_skipped",
            "cancelled": "worker_run_cancelled",
        }
        self.ui_stream.append(
            team_id=result.team_id,
            kind=kind_by_status[result.status],
            payload={
                key: value
                for key, value in {
                    "team_id": result.team_id,
                    "agent_id": result.agent_id,
                    "session_id": result.session_id,
                    "delivery_id": result.delivery_id,
                    "status": result.status,
                    "message_id": result.message_id,
                    "error": result.error,
                    "retry_status": result.retry_status,
                    "attempt": result.attempt,
                    "next_run_at": result.next_run_at,
                    "cancellation_status": result.cancellation_status,
                }.items()
                if value is not None
            },
            created_at=float(self._clock()),
        )

    def _matching_cancellation(
        self,
        session: TeamWorkerSession,
        *,
        delivery_id: str | None,
        message_id: str | None,
    ) -> TeamWorkerCancellationRecord | None:
        store = self._cancellation_store()
        if store is None:
            return None
        return store.match(
            team_id=session.team_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            delivery_id=delivery_id,
            message_id=message_id,
        )

    def _cancel_delivery(
        self,
        session: TeamWorkerSession,
        *,
        delivery: QueueDelivery | None,
        message_id: str | None,
        cancellation: TeamWorkerCancellationRecord,
    ) -> TeamWorkerRunResult:
        store = self._cancellation_store()
        updated = cancellation
        now = float(self._clock())
        if (
            store is not None
            and (
                cancellation.delivery_id is not None
                or cancellation.message_id is not None
            )
        ):
            updated = store.acknowledge(cancellation, now=now)
        if delivery is not None:
            self.message_queue.ack(session.agent_id, delivery.delivery_id)
        return TeamWorkerRunResult(
            team_id=session.team_id,
            agent_id=session.agent_id,
            session_id=session.session_id,
            delivery_id=(
                delivery.delivery_id
                if delivery is not None
                else cancellation.delivery_id or ""
            ),
            status="cancelled",
            message_id=message_id or cancellation.message_id,
            error=cancellation.reason,
            cancellation_status=updated.status,
        )

    def _collect_team_deliveries(
        self,
        session: TeamWorkerSession,
    ) -> list[QueueDelivery]:
        collect_team_messages = getattr(
            self.message_queue,
            "collect_team_messages",
            None,
        )
        if callable(collect_team_messages):
            return list(
                collect_team_messages(
                    session.agent_id,
                    team_id=session.team_id,
                ),
            )
        collect_matching = getattr(self.message_queue, "collect_matching", None)
        if callable(collect_matching):
            return list(
                collect_matching(
                    session.agent_id,
                    envelope_types=("team_message",),
                    predicate=lambda delivery: self._delivery_matches_session(
                        session,
                        delivery,
                    ),
                ),
            )
        raise TeamError(
            "team worker message queue must support team-scoped collection",
        )

    def _delivery_matches_session(
        self,
        session: TeamWorkerSession,
        delivery: QueueDelivery,
    ) -> bool:
        envelope = delivery.envelope
        if envelope.to_agent_id != session.agent_id:
            return False
        if envelope.type != "team_message" or not isinstance(
            envelope.payload,
            TeamMessage,
        ):
            return False
        if envelope.payload.team_id != session.team_id:
            return False
        return envelope.payload.to_agent_id in (None, session.agent_id)


@dataclass(frozen=True, slots=True)
class TeamWorkerDaemonState:
    """Snapshot of a team worker daemon lifecycle."""

    status: TeamWorkerDaemonStatus
    team_id: str | None
    poll_interval_seconds: float
    iterations: int = 0
    started_at: float | None = None
    stopped_at: float | None = None
    last_run_at: float | None = None
    last_results: tuple[TeamWorkerRunResult, ...] = ()
    errors: tuple[TeamWorkerRunError, ...] = ()
    retry_records: tuple[TeamWorkerRetryRecord, ...] = ()
    cancellation_records: tuple[TeamWorkerCancellationRecord, ...] = ()


class TeamWorkerDaemon:
    """Host loop that repeatedly runs pending team worker continuations."""

    def __init__(
        self,
        *,
        runner: TeamWorkerRunner,
        team_id: str | None = None,
        poll_interval_seconds: float = 0.5,
        clock: object | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        self.runner = runner
        self.team_id = team_id
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock if callable(clock) else time.time
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._state = TeamWorkerDaemonState(
            status="idle",
            team_id=team_id,
            poll_interval_seconds=poll_interval_seconds,
        )

    async def run_once(self) -> list[TeamWorkerRunResult]:
        """Process one daemon iteration without starting a background thread."""

        results = await self.runner.run_pending(team_id=self.team_id)
        self._record_run(results)
        return results

    def start(self) -> None:
        """Start the background polling loop if it is not already running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            now = float(self._clock())
            self._state = replace(
                self._state,
                status="running",
                started_at=now,
                stopped_at=None,
            )
            self._thread = Thread(
                target=self._run_loop,
                name=f"agentos-team-worker-daemon-{self.team_id or 'all'}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request the background polling loop to stop."""

        self._stop_event.set()
        with self._lock:
            if self._state.status == "running":
                self._state = replace(self._state, status="stopping")

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the background loop to exit."""

        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_running(self) -> bool:
        """Return whether the daemon thread is currently alive."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def state(self) -> TeamWorkerDaemonState:
        """Return an immutable daemon state snapshot."""

        with self._lock:
            return self._state

    def _run_loop(self) -> None:
        try:
            with asyncio.Runner() as runner:
                while not self._stop_event.is_set():
                    runner.run(self.run_once())
                    self._stop_event.wait(self.poll_interval_seconds)
        finally:
            with self._lock:
                self._state = replace(
                    self._state,
                    status="stopped",
                    stopped_at=float(self._clock()),
                )

    def _record_run(self, results: list[TeamWorkerRunResult]) -> None:
        errors = self.runner.errors()
        retry_records = self._retry_records()
        cancellation_records = self._cancellation_records()
        with self._lock:
            self._state = replace(
                self._state,
                iterations=self._state.iterations + 1,
                last_run_at=float(self._clock()),
                last_results=tuple(results),
                errors=tuple(errors),
                retry_records=retry_records,
                cancellation_records=cancellation_records,
            )

    def _retry_records(self) -> tuple[TeamWorkerRetryRecord, ...]:
        return tuple(self.runner.retry_records())

    def _cancellation_records(self) -> tuple[TeamWorkerCancellationRecord, ...]:
        return tuple(self.runner.cancellation_records())


class TeamStore(Protocol):
    """team records、members 和 messages 的 truth source。"""

    def create_team(self, team: TeamRecord) -> None:
        """创建 team。"""

    def get_team(self, team_id: str) -> TeamRecord | None:
        """返回 team。"""

    def mark_team_deleted(self, team_id: str, *, now: float) -> bool:
        """把 team 标记为 deleted。"""

    def add_member(self, member: TeamMemberRecord) -> None:
        """添加 team member。"""

    def get_member(self, team_id: str, agent_id: str) -> TeamMemberRecord | None:
        """返回成员记录。"""

    def list_members(self, team_id: str) -> list[TeamMemberRecord]:
        """返回 team 成员。"""

    def append_message(self, message: TeamMessage) -> None:
        """追加 team message。"""

    def list_messages(
        self,
        team_id: str,
        *,
        agent_id: str | None = None,
        after_message_id: str | None = None,
    ) -> list[TeamMessage]:
        """读取 team messages。"""


class InMemoryTeamStore:
    """线程安全的本地 team store。"""

    def __init__(self) -> None:
        self._teams: dict[str, TeamRecord] = {}
        self._members: dict[str, dict[str, TeamMemberRecord]] = {}
        self._messages: dict[str, list[TeamMessage]] = {}
        self._lock = RLock()

    def create_team(self, team: TeamRecord) -> None:
        with self._lock:
            if team.team_id in self._teams:
                raise ValueError(f"team already exists: {team.team_id}")
            self._teams[team.team_id] = team
            self._members.setdefault(team.team_id, {})
            self._messages.setdefault(team.team_id, [])

    def get_team(self, team_id: str) -> TeamRecord | None:
        with self._lock:
            return self._teams.get(team_id)

    def mark_team_deleted(self, team_id: str, *, now: float) -> bool:
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                return False
            self._teams[team_id] = replace(team, status="deleted")
            return True

    def add_member(self, member: TeamMemberRecord) -> None:
        with self._lock:
            team = self._teams.get(member.team_id)
            if team is None or team.status != "active":
                raise TeamNotFoundError(member.team_id)
            self._members.setdefault(member.team_id, {})[member.agent_id] = member

    def get_member(self, team_id: str, agent_id: str) -> TeamMemberRecord | None:
        with self._lock:
            return self._members.get(team_id, {}).get(agent_id)

    def list_members(self, team_id: str) -> list[TeamMemberRecord]:
        with self._lock:
            return list(self._members.get(team_id, {}).values())

    def append_message(self, message: TeamMessage) -> None:
        with self._lock:
            team = self._teams.get(message.team_id)
            if team is None or team.status != "active":
                raise TeamNotFoundError(message.team_id)
            self._messages.setdefault(message.team_id, []).append(message)

    def list_messages(
        self,
        team_id: str,
        *,
        agent_id: str | None = None,
        after_message_id: str | None = None,
    ) -> list[TeamMessage]:
        with self._lock:
            messages = list(self._messages.get(team_id, []))
        if after_message_id is not None:
            messages = _after_message(messages, after_message_id)
        if agent_id is None:
            return messages
        return [
            message
            for message in messages
            if (
                message.to_agent_id is None
                and message.from_agent_id != agent_id
            )
            or message.to_agent_id == agent_id
        ]


def _after_message(
    messages: list[TeamMessage],
    after_message_id: str,
) -> list[TeamMessage]:
    for index, message in enumerate(messages):
        if message.message_id == after_message_id:
            return messages[index + 1 :]
    return messages


class TeamWakeupTrigger(Protocol):
    """team message 后的 continuation wakeup 边界。"""

    def on_team_message(
        self,
        recipient_agent_id: str,
        team_id: str,
        message_id: str,
    ) -> None:
        """通知 recipient 有新的 team message。"""


@dataclass(slots=True)
class LocalTeamWakeupTrigger:
    """只写 notice store 的本地 team wakeup trigger。"""

    notice_store: TeamNoticeStore

    def on_team_message(
        self,
        recipient_agent_id: str,
        team_id: str,
        message_id: str,
    ) -> None:
        """把 team message 写成 continuation notice。"""

        self.notice_store.add_team_message(
            recipient_agent_id,
            team_id=team_id,
            message_id=message_id,
        )


class TeamRuntime:
    """team record/message runtime，不直接运行 agent。"""

    def __init__(
        self,
        *,
        store: TeamStore,
        message_queue: AgentMessageQueue,
        notice_store: TeamNoticeStore | None = None,
        wakeup_trigger: TeamWakeupTrigger | None = None,
        worker_session_provider: TeamWorkerSessionProvider | None = None,
        ui_stream: TeamUiStreamStore | None = None,
        clock: object | None = None,
        id_factory: object | None = None,
    ) -> None:
        self.store = store
        self.message_queue = message_queue
        self.notice_store = notice_store or TeamNoticeStore()
        self.wakeup_trigger = wakeup_trigger or LocalTeamWakeupTrigger(
            self.notice_store,
        )
        self.worker_session_provider = worker_session_provider
        self.ui_stream = ui_stream
        self._clock = clock if callable(clock) else time.time
        self._id_factory = id_factory if callable(id_factory) else self._default_id

    def create_team(
        self,
        *,
        team_id: str | None = None,
        name: str,
        description: str,
        leader_agent_id: str,
        leader_session_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> TeamRecord:
        """创建 team 并注册 leader member。"""

        now = float(self._clock())
        resolved_team_id = team_id or str(self._id_factory("team"))
        team = TeamRecord(
            team_id=resolved_team_id,
            name=name,
            description=description,
            leader_agent_id=leader_agent_id,
            created_at=now,
            workspace=workspace,
        )
        self.store.create_team(team)
        self.store.add_member(
            TeamMemberRecord(
                team_id=resolved_team_id,
                agent_id=leader_agent_id,
                role="leader",
                session_id=leader_session_id,
                workspace=workspace,
                created_at=now,
            ),
        )
        self.message_queue.create_inbox(leader_agent_id)
        self._publish_ui_event(
            team.team_id,
            "team_created",
            {
                "team_id": team.team_id,
                "name": team.name,
                "description": team.description,
                "leader_agent_id": team.leader_agent_id,
                "leader_session_id": leader_session_id,
                "status": team.status,
            },
            now=now,
        )
        return team

    def add_member(
        self,
        *,
        team_id: str,
        agent_id: str,
        role: TeamMemberRole = "worker",
        session_id: str | None = None,
        capabilities: tuple[str, ...] = (),
        workspace: WorkspaceHandle | None = None,
    ) -> TeamMemberRecord:
        """向 active team 添加成员并创建 inbox。"""

        team = self._require_active_team(team_id)
        now = float(self._clock())
        resolved_session_id = session_id
        resolved_workspace = workspace
        if role == "worker" and self.worker_session_provider is not None:
            worker_session = self.worker_session_provider.create_worker_session(
                TeamWorkerSessionRequest(
                    team_id=team_id,
                    agent_id=agent_id,
                    role=role,
                    capabilities=tuple(capabilities),
                    requested_session_id=session_id,
                    team_workspace=team.workspace,
                    requested_workspace=workspace,
                    created_by_agent_id=team.leader_agent_id,
                    created_at=now,
                ),
            )
            resolved_session_id = worker_session.session_id
            resolved_workspace = worker_session.workspace
        member = TeamMemberRecord(
            team_id=team_id,
            agent_id=agent_id,
            role=role,
            session_id=resolved_session_id,
            capabilities=tuple(capabilities),
            workspace=resolved_workspace,
            created_at=now,
        )
        self.store.add_member(member)
        self.message_queue.create_inbox(agent_id)
        self._publish_ui_event(
            team_id,
            "member_added",
            {
                "team_id": member.team_id,
                "agent_id": member.agent_id,
                "role": member.role,
                "session_id": member.session_id,
                "capabilities": list(member.capabilities),
                "status": member.status,
                "workspace_id": (
                    None
                    if member.workspace is None
                    else member.workspace.workspace_id
                ),
            },
            now=now,
        )
        return member

    def say(
        self,
        *,
        team_id: str,
        from_agent_id: str,
        content: str,
        to_agent_id: str | None = None,
        kind: TeamMessageKind = "observation",
        correlation_id: str | None = None,
        artifact_handles: tuple[str, ...] = (),
        metadata: Mapping[str, str] | None = None,
    ) -> TeamMessage:
        """保存 team message，并向收件人发送 wakeup hint。"""

        self._require_active_member(team_id, from_agent_id)
        recipients = self._recipients(team_id, from_agent_id, to_agent_id)
        message = TeamMessage(
            message_id=str(self._id_factory("team_message")),
            team_id=team_id,
            from_agent_id=from_agent_id,
            content=content,
            created_at=float(self._clock()),
            to_agent_id=to_agent_id,
            kind=kind,
            correlation_id=correlation_id,
            artifact_handles=tuple(artifact_handles),
            metadata=dict(metadata or {}),
        )
        self.store.append_message(message)
        self._publish_ui_event(
            team_id,
            "message_appended",
            {
                "team_id": message.team_id,
                "message_id": message.message_id,
                "from_agent_id": message.from_agent_id,
                "to_agent_id": message.to_agent_id,
                "content": message.content,
                "kind": message.kind,
                "correlation_id": message.correlation_id,
                "artifact_handles": list(message.artifact_handles),
                "metadata": dict(message.metadata),
            },
            now=message.created_at,
        )
        for recipient_id in recipients:
            envelope = AgentEnvelope(
                envelope_id=str(self._id_factory("env")),
                from_agent_id=from_agent_id,
                to_agent_id=recipient_id,
                type="team_message",
                payload=message,
                created_at=message.created_at,
                correlation_id=message.message_id,
            )
            self.message_queue.send(envelope)
            self.wakeup_trigger.on_team_message(
                recipient_id,
                team_id,
                message.message_id,
            )
        return message

    def messages_for(
        self,
        agent_id: str,
        team_id: str,
        *,
        after_message_id: str | None = None,
    ) -> list[TeamMessage]:
        """读取指定成员可见的 team messages，不 drain inbox。"""

        self._require_active_member(team_id, agent_id)
        return self.store.list_messages(
            team_id,
            agent_id=agent_id,
            after_message_id=after_message_id,
        )

    def delete_team(self, team_id: str) -> bool:
        """把 team 标记为 deleted。"""

        now = float(self._clock())
        if self.worker_session_provider is not None:
            self.worker_session_provider.close_worker_sessions(team_id, now=now)
        deleted = self.store.mark_team_deleted(team_id, now=now)
        if deleted:
            self._publish_ui_event(
                team_id,
                "team_deleted",
                {"team_id": team_id, "deleted": True},
                now=now,
            )
        return deleted

    def _require_active_team(self, team_id: str) -> TeamRecord:
        team = self.store.get_team(team_id)
        if team is None or team.status != "active":
            raise TeamNotFoundError(team_id)
        return team

    def _require_active_member(
        self,
        team_id: str,
        agent_id: str,
    ) -> TeamMemberRecord:
        self._require_active_team(team_id)
        member = self.store.get_member(team_id, agent_id)
        if member is None or member.status != "active":
            raise TeamMembershipError(agent_id)
        return member

    def _recipients(
        self,
        team_id: str,
        from_agent_id: str,
        to_agent_id: str | None,
    ) -> list[str]:
        if to_agent_id is not None:
            self._require_active_member(team_id, to_agent_id)
            return [to_agent_id]
        return [
            member.agent_id
            for member in self.store.list_members(team_id)
            if member.status == "active" and member.agent_id != from_agent_id
        ]

    def _default_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def _publish_ui_event(
        self,
        team_id: str,
        kind: TeamUiEventKind,
        payload: Mapping[str, object],
        *,
        now: float,
    ) -> None:
        if self.ui_stream is None:
            return
        self.ui_stream.append(
            team_id=team_id,
            kind=kind,
            payload=payload,
            created_at=now,
        )


class TeamTools:
    """Register TeamRuntime operations as external tools."""

    def __init__(
        self,
        *,
        runtime: TeamRuntime,
        owner_agent_id: str,
        authorization_policy: TeamToolAuthorizationPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.owner_agent_id = owner_agent_id
        self.authorization_policy = (
            authorization_policy or DefaultTeamToolAuthorizationPolicy()
        )

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            RegisteredTool(
                name="team_create",
                description="Create a team discussion with this agent as leader.",
                parameters=self._team_create_parameters(),
                handler=self._team_create,
            ),
        )
        registry.register(
            RegisteredTool(
                name="agent_create",
                description="Add an agent member to an existing team discussion.",
                parameters=self._agent_create_parameters(),
                handler=self._agent_create,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_say",
                description="Send a message from this agent into a team discussion.",
                parameters=self._team_say_parameters(),
                handler=self._team_say,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_read_messages",
                description="Read team messages visible to this agent.",
                parameters=self._team_read_messages_parameters(),
                handler=self._team_read_messages,
            ),
        )
        registry.register(
            RegisteredTool(
                name="team_delete",
                description="Mark a team discussion deleted.",
                parameters=self._team_delete_parameters(),
                handler=self._team_delete,
            ),
        )

    def _team_create(self, arguments: dict[str, object]) -> str:
        self._authorize("team_create", None)
        team = self.runtime.create_team(
            team_id=(
                str(arguments["team_id"])
                if arguments.get("team_id") is not None
                else None
            ),
            name=str(arguments["name"]),
            description=str(arguments["description"]),
            leader_agent_id=self.owner_agent_id,
            leader_session_id=(
                str(arguments["leader_session_id"])
                if arguments.get("leader_session_id") is not None
                else None
            ),
        )
        leader = self.runtime.store.get_member(team.team_id, self.owner_agent_id)
        return json.dumps(
            {
                "team": self._team_to_dict(team),
                "leader": None if leader is None else self._member_to_dict(leader),
            },
            sort_keys=True,
        )

    def _agent_create(self, arguments: dict[str, object]) -> str:
        team_id = str(arguments["team_id"])
        self._authorize("agent_create", team_id)
        self._require_leader(team_id)
        member = self.runtime.add_member(
            team_id=team_id,
            agent_id=str(arguments["agent_id"]),
            role=self._member_role(arguments.get("role", "worker")),
            session_id=(
                str(arguments["session_id"])
                if arguments.get("session_id") is not None
                else None
            ),
            capabilities=self._string_tuple(arguments.get("capabilities", ())),
        )
        return json.dumps(self._member_to_dict(member), sort_keys=True)

    def _team_say(self, arguments: dict[str, object]) -> str:
        team_id = str(arguments["team_id"])
        self._authorize("team_say", team_id)
        message = self.runtime.say(
            team_id=team_id,
            from_agent_id=self.owner_agent_id,
            content=str(arguments["content"]),
            to_agent_id=(
                str(arguments["to_agent_id"])
                if arguments.get("to_agent_id") is not None
                else None
            ),
            kind=self._message_kind(arguments.get("kind", "observation")),
            correlation_id=(
                str(arguments["correlation_id"])
                if arguments.get("correlation_id") is not None
                else None
            ),
            artifact_handles=self._string_tuple(
                arguments.get("artifact_handles", ()),
            ),
            metadata=self._string_mapping(arguments.get("metadata")),
        )
        return json.dumps(self._message_to_dict(message), sort_keys=True)

    def _team_read_messages(self, arguments: dict[str, object]) -> str:
        team_id = str(arguments["team_id"])
        self._authorize("team_read_messages", team_id)
        messages = self.runtime.messages_for(
            self.owner_agent_id,
            team_id,
            after_message_id=(
                str(arguments["after_message_id"])
                if arguments.get("after_message_id") is not None
                else None
            ),
        )
        return json.dumps(
            {
                "messages": [
                    self._message_to_dict(message)
                    for message in messages
                ],
            },
            sort_keys=True,
        )

    def _team_delete(self, arguments: dict[str, object]) -> str:
        team_id = str(arguments["team_id"])
        self._authorize("team_delete", team_id)
        self._require_leader(team_id)
        return json.dumps(
            {
                "team_id": team_id,
                "deleted": self.runtime.delete_team(team_id),
            },
            sort_keys=True,
        )

    def _authorize(self, tool_name: str, team_id: str | None) -> None:
        self.authorization_policy.authorize_team_tool(
            tool_name=tool_name,
            owner_agent_id=self.owner_agent_id,
            team_id=team_id,
        )

    def _require_leader(self, team_id: str) -> TeamMemberRecord:
        member = self.runtime._require_active_member(team_id, self.owner_agent_id)
        if member.role != "leader":
            raise TeamMembershipError(
                f"{self.owner_agent_id} must be team leader",
            )
        return member

    def _team_to_dict(self, team: TeamRecord) -> dict[str, object]:
        return {
            "team_id": team.team_id,
            "name": team.name,
            "description": team.description,
            "leader_agent_id": team.leader_agent_id,
            "created_at": team.created_at,
            "status": team.status,
            "workspace_id": (
                None if team.workspace is None else team.workspace.workspace_id
            ),
        }

    def _member_to_dict(self, member: TeamMemberRecord) -> dict[str, object]:
        return {
            "team_id": member.team_id,
            "agent_id": member.agent_id,
            "role": member.role,
            "session_id": member.session_id,
            "capabilities": list(member.capabilities),
            "status": member.status,
            "created_at": member.created_at,
            "workspace_id": (
                None if member.workspace is None else member.workspace.workspace_id
            ),
        }

    def _message_to_dict(self, message: TeamMessage) -> dict[str, object]:
        return {
            "message_id": message.message_id,
            "team_id": message.team_id,
            "from_agent_id": message.from_agent_id,
            "to_agent_id": message.to_agent_id,
            "content": message.content,
            "kind": message.kind,
            "created_at": message.created_at,
            "correlation_id": message.correlation_id,
            "artifact_handles": list(message.artifact_handles),
            "metadata": dict(message.metadata),
        }

    def _string_tuple(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, list | tuple):
            raise ValueError("expected list of strings")
        return tuple(str(item) for item in value)

    def _string_mapping(self, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("expected object with string values")
        return {str(key): str(item) for key, item in value.items()}

    def _member_role(self, value: object) -> TeamMemberRole:
        role = str(value)
        if role not in {"leader", "worker"}:
            raise ValueError(f"unsupported team member role: {role}")
        return cast(TeamMemberRole, role)

    def _message_kind(self, value: object) -> TeamMessageKind:
        kind = str(value)
        if kind not in {"instruction", "observation", "result", "notice"}:
            raise ValueError(f"unsupported team message kind: {kind}")
        return cast(TeamMessageKind, kind)

    def _team_create_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "leader_session_id": {"type": "string"},
            },
            "required": ["name", "description"],
        }

    def _agent_create_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "role": {"type": "string", "enum": ["leader", "worker"]},
                "session_id": {"type": "string"},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["team_id", "agent_id"],
        }

    def _team_say_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "content": {"type": "string"},
                "to_agent_id": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["instruction", "observation", "result", "notice"],
                },
                "correlation_id": {"type": "string"},
                "artifact_handles": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "metadata": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["team_id", "content"],
        }

    def _team_read_messages_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "after_message_id": {"type": "string"},
            },
            "required": ["team_id"],
        }

    def _team_delete_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
            },
            "required": ["team_id"],
        }
