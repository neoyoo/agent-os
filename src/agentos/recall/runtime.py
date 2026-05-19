from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentos.compression import CompressionIndex
from agentos.memory import MemoryRuntime
from agentos.messages import Message, MessageRuntime

if TYPE_CHECKING:
    from agentos.runtime.event_bus import EventBus
else:
    EventBus = object


class RecallContextError(ValueError):
    """Raised when compressed or semantic context recall fails."""


@dataclass(slots=True)
class RecallRuntime:
    """Resolve `recall_context` requests without mutating the active message window."""

    compression_index: CompressionIndex
    message_runtime: MessageRuntime
    memory_runtime: MemoryRuntime | None = None
    event_bus: EventBus | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def recall_context(
        self,
        handle: str | None = None,
        *,
        query: str | None = None,
        limit: int = 1,
    ) -> list[Message]:
        """Return original recalled messages for the caller to expose as tool output."""

        if (handle is None) == (query is None):
            raise RecallContextError("provide either handle or query for recall_context")
        if query is not None:
            return self._recall_by_query(query=query, limit=limit)
        if handle is None:
            raise RecallContextError("provide either handle or query for recall_context")

        return self._recall_by_handle(handle)

    def _recall_by_handle(self, handle: str) -> list[Message]:
        """Recall by compressed segment handle."""

        from agentos.runtime.event_bus import RecallContextRequestedEvent

        self._emit(
            RecallContextRequestedEvent(
                handle=handle,
                **self._event_context(),
            ),
        )
        try:
            source_message_ids = self.compression_index.source_refs(handle)
        except KeyError as error:
            message = f"unknown compressed segment: {handle}"
            from agentos.runtime.event_bus import RecallContextFailedEvent

            self._emit(
                RecallContextFailedEvent(
                    handle=handle,
                    error=message,
                    **self._event_context(),
                ),
            )
            raise RecallContextError(message) from error

        recalled_messages = [
            self.message_runtime.store.get(message_id)
            for message_id in source_message_ids
        ]
        from agentos.runtime.event_bus import RecallContextInjectedEvent

        self._emit(
            RecallContextInjectedEvent(
                handle=handle,
                message_ids=tuple(source_message_ids),
                **self._event_context(),
            ),
        )
        return recalled_messages

    def _recall_by_query(self, query: str, limit: int) -> list[Message]:
        """Recall source messages via semantic memory."""

        if self.memory_runtime is None:
            raise RecallContextError("memory runtime is required for query recall")
        if self.session_id is None:
            raise RecallContextError("session_id is required for query recall")

        event_handle = f"query:{query}"
        from agentos.runtime.event_bus import RecallContextRequestedEvent

        self._emit(
            RecallContextRequestedEvent(
                handle=event_handle,
                **self._event_context(),
            ),
        )
        recalled_messages = self.memory_runtime.recall_by_query(
            self.session_id,
            query,
            limit,
        )
        self.message_runtime.hydrate_messages(recalled_messages)
        from agentos.runtime.event_bus import RecallContextInjectedEvent

        self._emit(
            RecallContextInjectedEvent(
                handle=event_handle,
                message_ids=tuple(message.id for message in recalled_messages),
                **self._event_context(),
            ),
        )
        return recalled_messages

    def _emit(self, event: object) -> None:
        """Emit recall events when an EventBus is configured."""

        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _event_context(self) -> dict[str, str | None]:
        """Return recall event session/turn identifiers."""

        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }
