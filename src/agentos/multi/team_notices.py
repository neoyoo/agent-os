from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock

from agentos.runtime.continuation import ContinuationNotice


class TeamNoticeProvider:
    """Bind one agent to its typed team continuation notices."""

    def __init__(self, store: TeamNoticeStore, agent_id: str) -> None:
        self._store = store
        self._agent_id = agent_id

    def consume_notices(self) -> tuple[ContinuationNotice, ...]:
        return self._store.consume_notices(self._agent_id)


class TeamNoticeStore:
    """Store typed team-message continuation facts by recipient agent."""

    def __init__(self) -> None:
        self._notices: dict[str, deque[ContinuationNotice]] = defaultdict(deque)
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
        del team_id
        notice = ContinuationNotice(
            kind="team_message",
            subject_id=message_id,
            action="team_read_messages",
        )
        with self._lock:
            self._notices[agent_id].append(notice)

    def consume_notices(
        self,
        agent_id: str,
    ) -> tuple[ContinuationNotice, ...]:
        with self._lock:
            notices = tuple(self._notices[agent_id])
            self._notices[agent_id].clear()
            return notices


__all__ = ["TeamNoticeProvider", "TeamNoticeStore"]
