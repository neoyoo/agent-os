from __future__ import annotations

from agentos.channels._sync_adapter import KeyedSyncWorkBoundary, run_sync_call
from agentos.persistence import BackendUnavailableError


class RedisSseSyncClient:
    """按取消安全的键顺序执行阻塞式 Redis SSE 操作。"""

    def __init__(self, client: object) -> None:
        self._client = client
        self._mutations = KeyedSyncWorkBoundary()

    async def call(self, operation: str, /, *args: object, **kwargs: object) -> object:
        try:
            return await run_sync_call(self._invoke, operation, *args, **kwargs)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    async def mutate(
        self,
        key: str,
        operation: str,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        try:
            return await self._mutations.run(
                key,
                self._invoke,
                operation,
                *args,
                **kwargs,
            )
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    async def expire(self, key: str, ttl_seconds: int) -> None:
        await self.mutate(key, "expire", key, ttl_seconds)

    async def delete(self, key: str) -> None:
        try:
            await self._mutations.drain(key, self._invoke, "delete", key)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _invoke(self, operation: str, *args: object, **kwargs: object) -> object:
        func = getattr(self._client, operation, None)
        if not callable(func):
            raise BackendUnavailableError("Redis backend unavailable")
        return func(*args, **kwargs)
