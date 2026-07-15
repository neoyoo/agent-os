from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from agentos.context import CompressedSegment
from agentos.messages import MessageRef, StoredMessage
from agentos.persistence.session_store import HotSessionState
from agentos.recall.types import CompressedSegmentPackage
from agentos.runtime.session import SessionState


@dataclass(slots=True)
class InMemoryHotSessionStore:
    """测试和 local profile 使用的热点 session store。"""

    _states: dict[str, HotSessionState] = field(default_factory=dict)
    _messages: dict[str, dict[str, StoredMessage]] = field(default_factory=dict)
    _segment_refs: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    _temporary_refs: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def load_hot_state(self, session_id: str) -> HotSessionState | None:
        """读取热点 session state。"""

        return self._states.get(session_id)

    def save_hot_state(self, state: HotSessionState) -> None:
        """保存热点 session state，并同步其中携带的消息和 refs。"""

        self._states[state.session_id] = state
        for message in state.recent_messages:
            self.append_hot_message(state.session_id, message)
        for segment_id, refs in state.segment_refs.items():
            self.save_segment_refs(state.session_id, segment_id, refs)
        self.set_temporary_recalled_refs(
            state.session_id,
            state.temporary_recalled_refs,
        )

    def append_hot_message(self, session_id: str, message: StoredMessage) -> None:
        """追加一条热点原文消息。"""

        self._messages.setdefault(session_id, {})[message.id] = message

    def get_hot_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage] | None:
        """按 ids 读取热点消息；任一缺失返回 None。"""

        session_messages = self._messages.get(session_id, {})
        messages: list[StoredMessage] = []
        for message_id in message_ids:
            message = session_messages.get(message_id)
            if message is None:
                return None
            messages.append(message)
        return messages

    def save_segment_refs(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """保存热点 segment refs。"""

        self._segment_refs.setdefault(session_id, {})[segment_id] = tuple(message_ids)

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...] | None:
        """读取热点 segment refs。"""

        return self._segment_refs.get(session_id, {}).get(segment_id)

    def set_temporary_recalled_refs(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """设置一次性 recalled refs。"""

        self._temporary_refs[session_id] = tuple(message_ids)

    def consume_temporary_recalled_refs(self, session_id: str) -> tuple[str, ...]:
        """消费并清空一次性 recalled refs。"""

        return self._temporary_refs.pop(session_id, ())


@dataclass(slots=True)
class InMemoryDurableSessionStore:
    """测试和 local profile 使用的 durable session store。"""

    _sessions: dict[str, SessionState] = field(default_factory=dict)
    _messages: dict[str, dict[str, StoredMessage]] = field(default_factory=dict)
    _active_refs: dict[str, tuple[MessageRef, ...]] = field(default_factory=dict)
    _packages: dict[str, dict[str, CompressedSegmentPackage]] = field(
        default_factory=dict,
    )

    def save_session(self, session: SessionState) -> None:
        """保存 session state。"""

        self._sessions[session.id] = session

    def load_session(self, session_id: str) -> SessionState:
        """读取 session state。"""

        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise KeyError(session_id) from error

    def append_message(self, session_id: str, message: StoredMessage) -> None:
        """追加原始消息。"""

        self._messages.setdefault(session_id, {})[message.id] = message

    def get_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[StoredMessage]:
        """按 ids 读取原始消息。"""

        session_messages = self._messages.get(session_id, {})
        messages: list[StoredMessage] = []
        for message_id in message_ids:
            try:
                messages.append(session_messages[message_id])
            except KeyError as error:
                raise KeyError(message_id) from error
        return messages

    def save_active_refs(
        self,
        session_id: str,
        refs: Sequence[MessageRef],
    ) -> None:
        """保存 active refs。"""

        self._active_refs[session_id] = tuple(refs)

    def load_active_refs(self, session_id: str) -> tuple[MessageRef, ...]:
        """读取 active refs。"""

        return self._active_refs.get(session_id, ())

    def save_compressed_segment(
        self,
        session_id: str,
        package: CompressedSegmentPackage,
    ) -> None:
        """保存 compressed segment package。"""

        self._packages.setdefault(session_id, {})[package.segment.id] = package

    def get_segment_refs(self, session_id: str, segment_id: str) -> tuple[str, ...]:
        """读取 durable segment refs。"""

        try:
            return self._packages[session_id][segment_id].source_refs
        except KeyError as error:
            raise KeyError(segment_id) from error

    def list_compressed_segments(
        self,
        session_id: str,
    ) -> tuple[CompressedSegment, ...]:
        """列出 session 下的 compressed segments。"""

        return tuple(
            package.segment
            for package in self._packages.get(session_id, {}).values()
        )
