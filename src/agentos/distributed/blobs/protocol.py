from typing import Protocol


class BlobStore(Protocol):
    """跨 Worker 共享的 Artifact bytes 存储边界。"""

    async def put_if_absent(
        self,
        *,
        artifact_id: str,
        data: bytes,
    ) -> bool:
        """仅在内容不存在时写入，并返回是否完成新写入。"""

    async def read(self, *, artifact_id: str) -> bytes | None:
        """读取原始内容；内容不存在时返回 ``None``。"""

    async def delete(self, *, artifact_id: str) -> None:
        """幂等删除原始内容。"""

    async def close(self) -> None:
        """释放由实现持有的异步资源。"""


__all__ = ["BlobStore"]
