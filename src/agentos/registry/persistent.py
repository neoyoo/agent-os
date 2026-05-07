from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol, Sequence

from agentos.multi import AgentCard, AgentStatus
from agentos.registry.serializers import (
    affinity_from_dict,
    affinity_to_dict,
    record_from_dict,
    record_to_dict,
)
from agentos.registry.types import AgentRegistryRecord, SessionAffinity


Clock = Callable[[], float]


class AgentRegistryStore(Protocol):
    """Agent registry 的持久存储边界。"""

    def save_record(self, record: AgentRegistryRecord) -> None:
        """保存 agent 注册记录。"""

    def delete_record(self, agent_id: str) -> None:
        """删除 agent 注册记录。"""

    def load_record(self, agent_id: str) -> AgentRegistryRecord | None:
        """读取 agent 注册记录。"""

    def list_records(self) -> list[AgentRegistryRecord]:
        """列出所有 agent 注册记录。"""

    def save_affinity(self, affinity: SessionAffinity) -> None:
        """保存 session affinity。"""

    def load_affinity(self, session_id: str) -> SessionAffinity | None:
        """读取 session affinity。"""

    def delete_affinity(self, session_id: str) -> None:
        """删除 session affinity。"""


class InMemoryAgentRegistryStore:
    """测试和 local profile 使用的 registry store。"""

    def __init__(self) -> None:
        self._records: dict[str, AgentRegistryRecord] = {}
        self._affinity: dict[str, SessionAffinity] = {}
        self._lock = RLock()

    def save_record(self, record: AgentRegistryRecord) -> None:
        """保存 agent 注册记录。"""

        with self._lock:
            self._records[record.card.agent_id] = record

    def delete_record(self, agent_id: str) -> None:
        """删除 agent 注册记录。"""

        with self._lock:
            self._records.pop(agent_id, None)

    def load_record(self, agent_id: str) -> AgentRegistryRecord | None:
        """读取 agent 注册记录。"""

        with self._lock:
            return self._records.get(agent_id)

    def list_records(self) -> list[AgentRegistryRecord]:
        """列出所有 agent 注册记录。"""

        with self._lock:
            return list(self._records.values())

    def save_affinity(self, affinity: SessionAffinity) -> None:
        """保存 session affinity。"""

        with self._lock:
            self._affinity[affinity.session_id] = affinity

    def load_affinity(self, session_id: str) -> SessionAffinity | None:
        """读取 session affinity。"""

        with self._lock:
            return self._affinity.get(session_id)

    def delete_affinity(self, session_id: str) -> None:
        """删除 session affinity。"""

        with self._lock:
            self._affinity.pop(session_id, None)


class JsonFileAgentRegistryStore:
    """JSON 文件 backed registry store，适合 single-process local deployment。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def save_record(self, record: AgentRegistryRecord) -> None:
        """保存 agent 注册记录。"""

        with self._lock:
            data = self._read()
            data["records"][record.card.agent_id] = record_to_dict(record)
            self._write(data)

    def delete_record(self, agent_id: str) -> None:
        """删除 agent 注册记录。"""

        with self._lock:
            data = self._read()
            data["records"].pop(agent_id, None)
            self._write(data)

    def load_record(self, agent_id: str) -> AgentRegistryRecord | None:
        """读取 agent 注册记录。"""

        with self._lock:
            raw = self._read()["records"].get(agent_id)
            return None if raw is None else record_from_dict(raw)

    def list_records(self) -> list[AgentRegistryRecord]:
        """列出所有 agent 注册记录。"""

        with self._lock:
            return [
                record_from_dict(record)
                for record in self._read()["records"].values()
            ]

    def save_affinity(self, affinity: SessionAffinity) -> None:
        """保存 session affinity。"""

        with self._lock:
            data = self._read()
            data["affinity"][affinity.session_id] = affinity_to_dict(affinity)
            self._write(data)

    def load_affinity(self, session_id: str) -> SessionAffinity | None:
        """读取 session affinity。"""

        with self._lock:
            raw = self._read()["affinity"].get(session_id)
            return None if raw is None else affinity_from_dict(raw)

    def delete_affinity(self, session_id: str) -> None:
        """删除 session affinity。"""

        with self._lock:
            data = self._read()
            data["affinity"].pop(session_id, None)
            self._write(data)

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {"records": {}, "affinity": {}}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)


class PersistentAgentRegistry:
    """带 heartbeat、健康过滤和 session affinity 的 AgentCard registry。"""

    def __init__(
        self,
        store: AgentRegistryStore,
        *,
        heartbeat_ttl_seconds: float = 30,
        session_affinity_ttl_seconds: float = 300,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self._session_affinity_ttl_seconds = session_affinity_ttl_seconds
        self._clock = clock or time.time

    def register(self, card: AgentCard) -> None:
        """注册或更新一个 agent card，并记录当前 heartbeat。"""

        self._store.save_record(
            AgentRegistryRecord(card=card, heartbeat_at=self._clock()),
        )

    def unregister(self, agent_id: str) -> None:
        """注销一个 agent card。"""

        self._store.delete_record(agent_id)

    def heartbeat(self, agent_id: str, status: AgentStatus = "idle") -> None:
        """刷新 agent heartbeat，并可同时更新 status。"""

        record = self._store.load_record(agent_id)
        if record is None:
            raise KeyError(agent_id)
        self._store.save_record(
            AgentRegistryRecord(
                card=replace(record.card, status=status),
                heartbeat_at=self._clock(),
            ),
        )

    def mark_unhealthy(self, agent_id: str) -> None:
        """把 agent 标记为不可健康路由。"""

        record = self._store.load_record(agent_id)
        if record is None:
            raise KeyError(agent_id)
        self._store.save_record(
            AgentRegistryRecord(
                card=replace(record.card, status="offline"),
                heartbeat_at=record.heartbeat_at,
            ),
        )

    def resolve(self, agent_id: str) -> AgentCard | None:
        """按 agent id 返回健康 card。"""

        record = self._store.load_record(agent_id)
        if record is None or not self._is_healthy(record):
            return None
        return record.card

    def discover(self, capabilities: Sequence[str]) -> list[AgentCard]:
        """按 capability 全量匹配健康 cards。"""

        required = set(capabilities)
        return [
            record.card
            for record in self._store.list_records()
            if self._is_healthy(record)
            and required.issubset(set(record.card.capabilities))
        ]

    def bind_session(self, session_id: str, agent_id: str) -> None:
        """绑定 session 到指定健康 agent。"""

        if self.resolve(agent_id) is None:
            raise KeyError(agent_id)
        self._store.save_affinity(
            SessionAffinity(
                session_id=session_id,
                agent_id=agent_id,
                expires_at=self._clock() + self._session_affinity_ttl_seconds,
            ),
        )

    def resolve_session(self, session_id: str) -> str | None:
        """解析 session affinity；过期或不健康时自动清理。"""

        affinity = self._store.load_affinity(session_id)
        if affinity is None:
            return None
        if affinity.expires_at <= self._clock():
            self._store.delete_affinity(session_id)
            return None
        if self.resolve(affinity.agent_id) is None:
            self._store.delete_affinity(session_id)
            return None
        return affinity.agent_id

    def _is_healthy(self, record: AgentRegistryRecord) -> bool:
        if record.card.status == "offline":
            return False
        return self._clock() - record.heartbeat_at <= self._heartbeat_ttl_seconds
