# Team Runtime And Distributed Wakeup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 5A team runtime primitives so leader and worker agents can coordinate through stored team messages, inbox wakeup hints, and continuation notices without sharing active messages.

**Architecture:** Add `agentos.multi.team` as the team boundary. `InMemoryTeamStore` is the truth source for team records, members, and messages; `AgentMessageQueue` is only a delivery/wakeup hint; `TeamNoticeStore` bridges team messages into continuation turns. `QueryLoop` and `AsyncQueryLoop` remain deployment-agnostic.

**Tech Stack:** Python 3.11 dataclasses/protocols, existing `AgentMessageQueue`, `AgentEnvelope`, `WorkspaceHandle`, pytest.

---

## Scope Contract

This plan implements only Phase 5A from `docs/superpowers/specs/2026-06-11-team-runtime-distributed-wakeup-design.md`.

Target conclusion:

```text
Team-style multi-agent coordination is not nested function calling.
Leader and worker agents are independent sessions coordinated by team records,
team messages, inbox notifications, wakeup notices, and explicit workspace/artifact handles.
QueryLoop remains unaware of team runtime.
```

Deferred:

- AgentScope-style worker session lifecycle.
- LLM tools: `team_create`, `agent_create`, `team_say`, `team_delete`.
- Planner / intent-router / subagent templates.
- Postgres team store.
- Automatic worker loop execution.
- UI stream protocol.

## File Structure

Create:

- `src/agentos/multi/team.py`  
  Team dataclasses, store protocol, in-memory store, notice store, wakeup trigger, and runtime.

- `tests/multi/test_team_runtime.py`  
  Team creation, member registration, directed/broadcast message visibility, wakeup notices, and no inbox drain regression.

Modify:

- `src/agentos/multi/types.py`  
  Add `team_message` envelope type and allow `TeamMessage` payload with forward-reference import hygiene.

- `src/agentos/multi/serializers.py`  
  Add `team_message_to_dict()`, `team_message_from_dict()`, and update envelope round-trip.

- `src/agentos/multi/__init__.py`  
  Export team public names.

- `src/agentos/__init__.py`  
  Mirror public team names at top level.

- `tests/multi/test_message_queue_contract.py`  
  Add regression proving team runtime does not drain task envelopes while reading team messages.

- `tests/multi/test_redis_message_queue.py`  
  Add `team_message` envelope serialization round-trip through Redis fake.

- `tests/architecture/test_public_api.py`  
  Add public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`  
  Update Team Discussion Agent readiness from pure future extension to primitives-ready after Phase 5A.

- `.claude/skills/agent-os/modules/multi-agent.md`  
  Add team runtime primitives and remaining gaps.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/channels/a2a.py`
- `src/agentos/channels/asgi.py`

## Task 1: Add Team Models And In-Memory Store

**Files:**
- Create: `src/agentos/multi/team.py`
- Create: `tests/multi/test_team_runtime.py`

- [ ] **Step 1: Write failing model/store tests**

Create `tests/multi/test_team_runtime.py`:

```python
from __future__ import annotations

from agentos.multi.team import InMemoryTeamStore, TeamMemberRecord, TeamRecord


def test_in_memory_team_store_creates_team_and_members() -> None:
    store = InMemoryTeamStore()
    team = TeamRecord(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        created_at=1.0,
    )
    leader = TeamMemberRecord(
        team_id="team_1",
        agent_id="leader",
        role="leader",
        session_id="session_leader",
        created_at=1.0,
    )
    worker = TeamMemberRecord(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        capabilities=("research",),
        session_id="session_worker",
        created_at=2.0,
    )

    store.create_team(team)
    store.add_member(leader)
    store.add_member(worker)

    assert store.get_team("team_1") == team
    assert store.get_member("team_1", "leader") == leader
    assert store.get_member("team_1", "worker") == worker
    assert store.list_members("team_1") == [leader, worker]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_in_memory_team_store_creates_team_and_members -q
```

Expected: FAIL with `ModuleNotFoundError` for `agentos.multi.team`.

- [ ] **Step 3: Implement dataclasses, protocol, and store**

Create `src/agentos/multi/team.py`:

```python
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from agentos.multi.message_queue import AgentMessageQueue
from agentos.multi.types import AgentEnvelope
from agentos.workspace import WorkspaceHandle


TeamStatus = Literal["active", "deleted"]
TeamMemberRole = Literal["leader", "worker"]
TeamMemberStatus = Literal["active", "offline", "removed"]
TeamMessageKind = Literal["instruction", "observation", "result", "notice"]


class TeamError(RuntimeError):
    """team runtime 基础错误。"""


class TeamNotFoundError(TeamError):
    """team 不存在或不可用。"""


class TeamMembershipError(TeamError):
    """agent 不是 team 成员或收件人无效。"""


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
            if message.to_agent_id is None
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
```

- [ ] **Step 4: Run model/store test**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_in_memory_team_store_creates_team_and_members -q
```

Expected: PASS.

## Task 2: Add Team Runtime, Notices, And Wakeup Hints

**Files:**
- Modify: `src/agentos/multi/team.py`
- Modify: `tests/multi/test_team_runtime.py`
- Modify: `src/agentos/multi/types.py`

- [ ] **Step 1: Add failing runtime tests**

Append to `tests/multi/test_team_runtime.py`:

```python
from pathlib import Path

from agentos.multi import AgentInbox
from agentos.multi.team import TeamNoticeStore, TeamRuntime
from agentos.workspace import LocalWorkspaceProvider, WorkspaceRequest


def test_team_runtime_sends_directed_message_and_wakeup_notice() -> None:
    store = InMemoryTeamStore()
    inbox = AgentInbox()
    notice_store = TeamNoticeStore()
    runtime = TeamRuntime(
        store=store,
        message_queue=inbox,
        notice_store=notice_store,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        leader_session_id="session_leader",
    )
    runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        session_id="session_worker",
        capabilities=("research",),
    )

    message = runtime.say(
        team_id="team_1",
        from_agent_id="leader",
        content="Find source A.",
        to_agent_id="worker",
        kind="instruction",
    )

    assert message.message_id == "team_message_1"
    assert runtime.messages_for("worker", "team_1") == [message]
    assert runtime.messages_for("leader", "team_1") == []
    deliveries = inbox.collect("worker")
    assert deliveries[0].envelope.type == "team_message"
    assert deliveries[0].envelope.payload == message
    assert notice_store.provider_for("worker").consume_notices() == (
        "Team team_1 received message team_message_1. Call read_team_messages to inspect it.",
    )


def test_team_runtime_adds_member_with_narrowed_workspace(tmp_path: Path) -> None:
    workspace_provider = LocalWorkspaceProvider(base_dir=tmp_path)
    team_workspace = workspace_provider.resolve_workspace(
        WorkspaceRequest(team_id="team_1", requested_scope="team"),
    )
    worker_workspace = workspace_provider.narrow_workspace(
        team_workspace,
        child_id="worker",
        scope="task",
    )
    runtime = TeamRuntime(
        store=InMemoryTeamStore(),
        message_queue=AgentInbox(),
        clock=lambda: 1.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        workspace=team_workspace,
    )
    member = runtime.add_member(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        workspace=worker_workspace,
    )

    assert member.workspace == worker_workspace
    assert member.workspace.parent_workspace_id == team_workspace.workspace_id
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_team_runtime_sends_directed_message_and_wakeup_notice tests/multi/test_team_runtime.py::test_team_runtime_adds_member_with_narrowed_workspace -q
```

Expected: FAIL with missing `TeamRuntime` / `TeamNoticeStore`, or invalid envelope type before `types.py` is updated.

- [ ] **Step 3: Extend envelope type**

Modify `src/agentos/multi/types.py`:

```python
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agentos.multi.team import TeamMessage
```

Change:

```python
AgentEnvelopeType = Literal["task_request", "task_result", "team_message"]
```

Change `AgentEnvelope.payload`:

```python
payload: TaskRequest | TaskResult | "TeamMessage"
```

- [ ] **Step 4: Implement notices and runtime**

Append to `src/agentos/multi/team.py`:

```python
class TeamNoticeProvider:
    """绑定单个 agent 的 team notice provider。"""

    def __init__(self, store: "TeamNoticeStore", agent_id: str) -> None:
        self._store = store
        self._agent_id = agent_id

    def consume_notices(self) -> tuple[str, ...]:
        return self._store.consume_notices(self._agent_id)


class TeamNoticeStore:
    """保存 team message continuation notices。"""

    def __init__(self) -> None:
        self._notices: dict[str, deque[str]] = defaultdict(deque)
        self._lock = RLock()

    def provider_for(self, agent_id: str) -> TeamNoticeProvider:
        return TeamNoticeProvider(self, agent_id)

    def add_team_message(
        self,
        agent_id: str,
        *,
        team_id: str,
        message_id: str,
    ) -> None:
        notice = (
            f"Team {team_id} received message {message_id}. "
            "Call read_team_messages to inspect it."
        )
        with self._lock:
            self._notices[agent_id].append(notice)

    def consume_notices(self, agent_id: str) -> tuple[str, ...]:
        with self._lock:
            notices = tuple(self._notices[agent_id])
            self._notices[agent_id].clear()
            return notices


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
        clock: object | None = None,
        id_factory: object | None = None,
    ) -> None:
        self.store = store
        self.message_queue = message_queue
        self.notice_store = notice_store or TeamNoticeStore()
        self.wakeup_trigger = wakeup_trigger or LocalTeamWakeupTrigger(
            self.notice_store,
        )
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
        self._require_active_team(team_id)
        member = TeamMemberRecord(
            team_id=team_id,
            agent_id=agent_id,
            role=role,
            session_id=session_id,
            capabilities=tuple(capabilities),
            workspace=workspace,
            created_at=float(self._clock()),
        )
        self.store.add_member(member)
        self.message_queue.create_inbox(agent_id)
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
        self._require_active_member(team_id, agent_id)
        return self.store.list_messages(
            team_id,
            agent_id=agent_id,
            after_message_id=after_message_id,
        )

    def delete_team(self, team_id: str) -> bool:
        return self.store.mark_team_deleted(team_id, now=float(self._clock()))

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
```

- [ ] **Step 5: Run runtime tests**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py -q
```

Expected: PASS.

## Task 3: Serialize Team Messages In Envelopes

**Files:**
- Modify: `src/agentos/multi/serializers.py`
- Modify: `tests/multi/test_team_runtime.py`
- Modify: `tests/multi/test_redis_message_queue.py`

- [ ] **Step 1: Add failing serializer tests**

Append to `tests/multi/test_team_runtime.py`:

```python
from agentos.multi.serializers import (
    envelope_from_dict,
    envelope_to_dict,
    team_message_from_dict,
    team_message_to_dict,
)
from agentos.multi.types import AgentEnvelope
from agentos.multi.team import TeamMessage


def test_team_message_serializes_and_round_trips_in_envelope() -> None:
    message = TeamMessage(
        message_id="msg_1",
        team_id="team_1",
        from_agent_id="worker",
        to_agent_id="leader",
        content="I found evidence.",
        kind="result",
        created_at=3.0,
        correlation_id="task_1",
        artifact_handles=("artifact://one",),
        metadata={"confidence": "high"},
    )
    envelope = AgentEnvelope(
        envelope_id="env_1",
        from_agent_id="worker",
        to_agent_id="leader",
        type="team_message",
        payload=message,
        created_at=3.0,
        correlation_id="msg_1",
    )

    assert team_message_from_dict(team_message_to_dict(message)) == message
    assert envelope_from_dict(envelope_to_dict(envelope)) == envelope
```

Append to `tests/multi/test_redis_message_queue.py`:

```python
from agentos.multi.team import TeamMessage


def team_envelope() -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id="env_team_1",
        from_agent_id="leader",
        to_agent_id="worker",
        type="team_message",
        payload=TeamMessage(
            message_id="msg_1",
            team_id="team_1",
            from_agent_id="leader",
            to_agent_id="worker",
            content="Please inspect artifact.",
            kind="instruction",
            created_at=5.0,
        ),
        created_at=5.0,
        correlation_id="msg_1",
    )


def test_redis_queue_round_trips_team_message_envelope() -> None:
    client = FakeRedis()
    queue = RedisAgentMessageQueue(url="redis://unused", client=client)
    queue.create_inbox("worker")

    queue.send(team_envelope())

    assert queue.collect("worker")[0].envelope == team_envelope()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_team_message_serializes_and_round_trips_in_envelope tests/multi/test_redis_message_queue.py::test_redis_queue_round_trips_team_message_envelope -q
```

Expected: FAIL because serializers do not support `team_message`.

- [ ] **Step 3: Implement serializers**

Modify `src/agentos/multi/serializers.py`:

```python
from agentos.multi.team import TeamMessage, TeamMessageKind
```

Change:

```python
_ENVELOPE_TYPES: frozenset[str] = frozenset(
    {"task_request", "task_result", "team_message"},
)
_TEAM_MESSAGE_KINDS: frozenset[str] = frozenset(
    {"instruction", "observation", "result", "notice"},
)
```

Add:

```python
def team_message_to_dict(message: TeamMessage) -> JsonDict:
    """序列化 TeamMessage。"""

    return {
        "message_id": message.message_id,
        "team_id": message.team_id,
        "from_agent_id": message.from_agent_id,
        "to_agent_id": message.to_agent_id,
        "content": message.content,
        "kind": _team_message_kind(message.kind),
        "created_at": message.created_at,
        "correlation_id": message.correlation_id,
        "artifact_handles": list(message.artifact_handles),
        "metadata": {
            str(key): str(value)
            for key, value in dict(message.metadata).items()
        },
    }


def team_message_from_dict(data: JsonDict) -> TeamMessage:
    """反序列化 TeamMessage。"""

    return TeamMessage(
        message_id=str(data["message_id"]),
        team_id=str(data["team_id"]),
        from_agent_id=str(data["from_agent_id"]),
        to_agent_id=(
            None if data.get("to_agent_id") is None else str(data["to_agent_id"])
        ),
        content=str(data["content"]),
        kind=_team_message_kind(data.get("kind", "observation")),
        created_at=float(data["created_at"]),
        correlation_id=(
            None if data.get("correlation_id") is None else str(data["correlation_id"])
        ),
        artifact_handles=tuple(
            str(handle) for handle in data.get("artifact_handles", [])
        ),
        metadata={
            str(key): str(value)
            for key, value in dict(data.get("metadata", {})).items()
        },
    )
```

Update `envelope_to_dict()`:

```python
    elif envelope_type == "team_message":
        if not isinstance(envelope.payload, TeamMessage):
            raise TypeError("team_message envelope payload must be TeamMessage")
        payload = team_message_to_dict(envelope.payload)
```

Update `envelope_from_dict()`:

```python
    elif envelope_type == "team_message":
        if not _is_team_message_payload(data["payload"]):
            raise TypeError("team_message envelope payload must be TeamMessage data")
        payload = team_message_from_dict(data["payload"])
```

Add helpers:

```python
def _team_message_kind(value: object) -> TeamMessageKind:
    kind = str(value)
    if kind not in _TEAM_MESSAGE_KINDS:
        raise ValueError(f"invalid team message kind: {kind}")
    return cast(TeamMessageKind, kind)


def _is_team_message_payload(value: object) -> bool:
    return _payload_has_keys(
        value,
        ("message_id", "team_id", "from_agent_id", "content"),
    )
```

- [ ] **Step 4: Run serializer tests**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_team_message_serializes_and_round_trips_in_envelope tests/multi/test_redis_message_queue.py::test_redis_queue_round_trips_team_message_envelope -q
```

Expected: PASS.

## Task 4: Prove Team Reads Do Not Drain Task Envelopes

**Files:**
- Modify: `tests/multi/test_team_runtime.py`

- [ ] **Step 1: Add failing regression test**

Append to `tests/multi/test_team_runtime.py`:

```python
from agentos.multi.types import TaskRequest


def test_team_messages_for_does_not_drain_task_envelopes() -> None:
    store = InMemoryTeamStore()
    inbox = AgentInbox()
    runtime = TeamRuntime(
        store=store,
        message_queue=inbox,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    runtime.create_team(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
    )
    runtime.add_member(team_id="team_1", agent_id="worker", role="worker")
    inbox.send(
        AgentEnvelope(
            envelope_id="env_task_1",
            from_agent_id="leader",
            to_agent_id="worker",
            type="task_request",
            payload=TaskRequest(task_id="task_1", instruction="Do work"),
            created_at=11.0,
            correlation_id="task_1",
        ),
    )

    assert runtime.messages_for("worker", "team_1") == []
    deliveries = inbox.collect("worker")

    assert [delivery.envelope.type for delivery in deliveries] == ["task_request"]
```

- [ ] **Step 2: Run regression test**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py::test_team_messages_for_does_not_drain_task_envelopes -q
```

Expected: PASS if `messages_for()` reads from `TeamStore`; FAIL if someone drains the queue.

## Task 5: Public API Exports

**Files:**
- Modify: `src/agentos/multi/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add failing public API assertions**

In `tests/architecture/test_public_api.py`, extend `test_phase8_multi_agent_public_api_exports` multi list with:

```python
        "InMemoryTeamStore",
        "LocalTeamWakeupTrigger",
        "TeamError",
        "TeamMemberRecord",
        "TeamMessage",
        "TeamNoticeProvider",
        "TeamNoticeStore",
        "TeamRecord",
        "TeamRuntime",
        "TeamStore",
        "TeamWakeupTrigger",
```

Extend the top-level `agentos` list with:

```python
        "InMemoryTeamStore",
        "TeamMemberRecord",
        "TeamMessage",
        "TeamNoticeStore",
        "TeamRecord",
        "TeamRuntime",
```

- [ ] **Step 2: Run public API test to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: FAIL because names are not exported.

- [ ] **Step 3: Export names**

In `src/agentos/multi/__init__.py`, import from `agentos.multi.team`:

```python
from agentos.multi.team import (
    InMemoryTeamStore,
    LocalTeamWakeupTrigger,
    TeamError,
    TeamMemberRecord,
    TeamMembershipError,
    TeamMessage,
    TeamMessageKind,
    TeamMemberRole,
    TeamMemberStatus,
    TeamNotFoundError,
    TeamNoticeProvider,
    TeamNoticeStore,
    TeamRecord,
    TeamRuntime,
    TeamStatus,
    TeamStore,
    TeamWakeupTrigger,
)
```

Add those names to `__all__`.

In `src/agentos/__init__.py`, mirror the stable public names:

```python
    InMemoryTeamStore,
    TeamMemberRecord,
    TeamMessage,
    TeamNoticeStore,
    TeamRecord,
    TeamRuntime,
```

and add them to top-level `__all__`.

- [ ] **Step 4: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_phase8_multi_agent_public_api_exports -q
```

Expected: PASS.

## Task 6: Documentation Alignment

**Files:**
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Update agent forms docs**

In `.claude/skills/agent-os/modules/agent-forms.md`, change the future extension row:

```markdown
| Team Discussion Agent | Team records/messages, in-memory store, and wakeup notices are primitives-ready; team tools, worker session lifecycle, and distributed team store remain future work |
```

Add a primitives-ready form near Distributed Multi-Agent Task Agent:

```markdown
### Team Discussion Agent
**Available**: `TeamRuntime`, `TeamRecord`, `TeamMemberRecord`, `TeamMessage`, `InMemoryTeamStore`, `TeamNoticeStore`, `AgentMessageQueue` wakeup hints, workspace handles on teams/members
**Missing**: first-class team tools, worker session lifecycle, distributed team store, planner integration, UI stream protocol
**Workaround**: use `TeamRuntime` as the conversation/state boundary and let app/profile code own worker agent sessions.
```

- [ ] **Step 2: Update multi-agent docs**

In `.claude/skills/agent-os/modules/multi-agent.md`, update readiness:

```markdown
| Team discussion records/messages/wakeup | Primitives ready via `TeamRuntime` |
| Team discussion with automatic worker sessions | Future extension |
```

Add a short "Team Runtime" section:

```markdown
## Team Runtime

`TeamRuntime` stores team records, members, and messages in `TeamStore`, then uses `AgentMessageQueue` only as a wakeup hint. `messages_for()` reads from the store and does not drain task envelopes.

Phase 5A does not run worker sessions automatically. App/profile code still owns worker session lifecycle and should pass narrowed `WorkspaceHandle` values for workers.
```

- [ ] **Step 3: Run docs drift search**

Run:

```bash
rg -n "Team Discussion Agent|team discussion|TeamRuntime|team_create|agent_create|team_say|team_delete" .claude docs src tests
```

Expected: docs should say team records/messages/wakeup are primitives-ready, while team tools, worker lifecycle, and distributed team store are future work. Planner primitives are handled by the separate Phase 6A planner slice.

## Task 7: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/multi/test_team_runtime.py tests/multi/test_redis_message_queue.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Windows line-ending warnings are acceptable if no error lines are printed.

- [ ] **Step 5: Boundary search**

Run:

```bash
rg "agentos.multi.team|TeamRuntime|TeamMessage|AgentMessageQueue|Redis|Postgres|A2A|workspace" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
```

Expected: no matches.

## Self-Review

Spec coverage:

- Team model/store: Task 1.
- Runtime wakeup and notices: Task 2.
- Envelope serialization: Task 3.
- No inbox drain invariant: Task 4.
- Public API: Task 5.
- Skill docs: Task 6.
- Verification and QueryLoop boundary: Task 7.

Placeholder scan:

- No implementation step contains "TBD", "TODO", or unspecified tests.
- Deferred work is explicitly listed in the scope contract.

Type consistency:

- Team names match the design spec: `TeamRuntime`, `TeamRecord`, `TeamMemberRecord`, `TeamMessage`, `TeamStore`, `InMemoryTeamStore`, `TeamNoticeStore`.
- Envelope type is consistently `team_message`.
