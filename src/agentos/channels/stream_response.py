from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CloseableSseResponse(Protocol):
    """ASGI 发送器消费的最小 closeable SSE 合同。"""

    status_code: int
    headers: tuple[tuple[str, str], ...]

    async def next_frame(self) -> bytes: ...

    async def aclose(self) -> None: ...


__all__ = ["CloseableSseResponse"]
