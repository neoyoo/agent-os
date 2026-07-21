from __future__ import annotations

import asyncio
import math

from agentos.distributed.models import ReplayItem, StreamGap
from agentos.distributed.protocols import EventSubscription
from agentos.transports.a2a.message_types import A2ATaskState
from agentos.transports.a2a.operation_types import (
    A2ARequestId,
    A2AStreamResponse,
)
from agentos.transports.a2a.sse import (
    encode_a2a_event,
    encode_a2a_gap,
    encode_a2a_heartbeat,
    encode_a2a_initial_response,
    is_a2a_terminal_event,
)
from agentos.transports.run_stream import encode_cursor


_TERMINAL_TASK_STATES = frozenset(
    {
        A2ATaskState.TASK_STATE_COMPLETED,
        A2ATaskState.TASK_STATE_FAILED,
        A2ATaskState.TASK_STATE_CANCELED,
        A2ATaskState.TASK_STATE_REJECTED,
    },
)


class A2ASseResponse:
    """输出 snapshot-first A2A SSE，并幂等释放底层事件订阅。"""

    __slots__ = (
        "_closed",
        "_close_after_initial",
        "_heartbeat_interval",
        "_initial_response",
        "_next_task",
        "_opaque_request_id",
        "_request_id",
        "_subscription",
    )

    status_code = 200
    headers = (
        ("Content-Type", "text/event-stream"),
        ("Cache-Control", "no-cache"),
        ("X-Accel-Buffering", "no"),
    )

    def __init__(
        self,
        subscription: EventSubscription | None,
        *,
        request_id: A2ARequestId,
        opaque_request_id: str,
        initial_response: A2AStreamResponse,
        heartbeat_interval: float,
        close_after_initial: bool = False,
    ) -> None:
        if type(initial_response) is not A2AStreamResponse:
            raise TypeError("initial_response must be A2AStreamResponse")
        if type(opaque_request_id) is not str or not opaque_request_id:
            raise ValueError("opaque_request_id must be a non-empty string")
        if type(close_after_initial) is not bool:
            raise TypeError("close_after_initial must be bool")
        if (
            type(heartbeat_interval) not in (int, float)
            or not math.isfinite(heartbeat_interval)
            or heartbeat_interval <= 0
        ):
            raise ValueError("heartbeat_interval must be positive")
        closes_after_initial = (
            close_after_initial or _closes_after_initial(initial_response)
        )
        if closes_after_initial and subscription is not None:
            raise ValueError("initial-only response must not have a subscription")
        if not closes_after_initial and subscription is None:
            raise ValueError("non-terminal task response requires a subscription")
        self._subscription = subscription
        self._request_id = request_id
        self._opaque_request_id = opaque_request_id
        self._initial_response: A2AStreamResponse | None = initial_response
        self._close_after_initial = closes_after_initial
        self._heartbeat_interval = float(heartbeat_interval)
        self._next_task: asyncio.Task[ReplayItem | StreamGap] | None = None
        self._closed = False

    async def next_frame(self) -> bytes:
        """返回下一帧；首帧永远是没有 SSE id 的 PostgreSQL snapshot。"""

        if self._closed:
            raise StopAsyncIteration
        initial = self._initial_response
        if initial is not None:
            self._initial_response = None
            try:
                frame = encode_a2a_initial_response(self._request_id, initial)
            except Exception:
                await self.aclose()
                raise
            if self._close_after_initial:
                await self.aclose()
            return frame.encode("utf-8")

        subscription = self._subscription
        if subscription is None:
            await self.aclose()
            raise StopAsyncIteration
        if self._next_task is None:
            self._next_task = asyncio.create_task(anext(subscription))
        task = self._next_task
        try:
            done, _ = await asyncio.wait(
                (task,),
                timeout=self._heartbeat_interval,
            )
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if not done:
            return encode_a2a_heartbeat().encode("utf-8")
        self._next_task = None
        try:
            item = task.result()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException:
            await self.aclose()
            raise
        try:
            frame, closes_stream = self._encode_item(item)
        except Exception:
            await self.aclose()
            raise
        if closes_stream:
            await self.aclose()
        return frame.encode("utf-8")

    async def aclose(self) -> None:
        """取消未完成的读取，并恰好一次关闭底层 subscription。"""

        if self._closed:
            return
        self._closed = True
        task = self._next_task
        self._next_task = None
        subscription = self._subscription
        self._subscription = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            finally:
                if subscription is not None:
                    await subscription.aclose()
            return
        if subscription is not None:
            await subscription.aclose()

    def _encode_item(self, item: ReplayItem | StreamGap) -> tuple[str, bool]:
        if type(item) is StreamGap:
            return (
                encode_a2a_gap(
                    item,
                    request_id=self._request_id,
                    opaque_request_id=self._opaque_request_id,
                ),
                True,
            )
        if type(item) is not ReplayItem:
            raise TypeError("event subscription returned an invalid item")
        envelope = item.event
        cursor = encode_cursor(
            tenant_id=envelope.tenant_id,
            session_id=envelope.session_id,
            run_id=envelope.run_id,
            position=item.cursor,
        )
        return (
            encode_a2a_event(self._request_id, cursor, envelope),
            is_a2a_terminal_event(envelope),
        )


def _closes_after_initial(initial: A2AStreamResponse) -> bool:
    if initial.message is not None:
        return True
    task = initial.task
    if task is None:
        raise ValueError("initial response must contain a task or message")
    return task.status.state in _TERMINAL_TASK_STATES


__all__ = ["A2ASseResponse"]
