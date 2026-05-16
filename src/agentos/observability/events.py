from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone

from agentos.events import AgentEvent, EventSubscriber as EventSubscriber


@dataclass(frozen=True, slots=True)
class EventRecord:
    """append-only runtime event 记录。"""

    sequence: int
    event_type: str
    session_id: str | None
    turn_id: str | None
    payload: dict[str, object]
    created_at: str


@dataclass(slots=True)
class EventLog:
    """内存中的 append-only event log。"""

    records: list[EventRecord] = field(default_factory=list)

    def record(self, event: AgentEvent) -> None:
        """把 typed event 追加为 EventRecord。"""

        payload = asdict(event) if is_dataclass(event) else {}
        self.records.append(
            EventRecord(
                sequence=len(self.records) + 1,
                event_type=type(event).__name__,
                session_id=getattr(event, "session_id", None),
                turn_id=getattr(event, "turn_id", None),
                payload=payload,
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
        )


def event_record_to_dict(record: EventRecord) -> dict[str, object]:
    """序列化 EventRecord。"""

    return {
        "sequence": record.sequence,
        "event_type": record.event_type,
        "session_id": record.session_id,
        "turn_id": record.turn_id,
        "payload": dict(record.payload),
        "created_at": record.created_at,
    }


def event_record_from_dict(data: dict[str, object]) -> EventRecord:
    """反序列化 EventRecord。"""

    return EventRecord(
        sequence=int(data["sequence"]),
        event_type=str(data["event_type"]),
        session_id=(
            None
            if data.get("session_id") is None
            else str(data.get("session_id"))
        ),
        turn_id=None if data.get("turn_id") is None else str(data.get("turn_id")),
        payload=dict(data.get("payload", {})),
        created_at=str(data["created_at"]),
    )
