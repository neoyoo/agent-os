from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentos.compression import CompressionIndex
from agentos.messages import Message, MessageRuntime

if TYPE_CHECKING:
    from agentos.runtime.event_bus import EventBus
else:
    EventBus = object


class RecallContextError(ValueError):
    """召回压缩片段失败。"""


@dataclass(slots=True)
class RecallRuntime:
    """执行 `recall_context` 并注入一次性原文消息。"""

    compression_index: CompressionIndex
    message_runtime: MessageRuntime
    event_bus: EventBus | None = None
    session_id: str | None = None
    turn_id: str | None = None

    def recall_context(self, handle: str) -> list[Message]:
        """恢复压缩片段对应的原始消息，并供下一次请求使用。"""

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
            raise RecallContextError(
                message,
            ) from error

        self.message_runtime.inject_temporary_recalled(source_message_ids)
        from agentos.runtime.event_bus import RecallContextInjectedEvent

        self._emit(
            RecallContextInjectedEvent(
                handle=handle,
                message_ids=tuple(source_message_ids),
                **self._event_context(),
            ),
        )
        return [
            self.message_runtime.store.get(message_id)
            for message_id in source_message_ids
        ]

    def _emit(self, event: object) -> None:
        """向 EventBus 写入 recall event。"""

        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _event_context(self) -> dict[str, str | None]:
        """返回 recall event 使用的 session/turn id。"""

        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }
