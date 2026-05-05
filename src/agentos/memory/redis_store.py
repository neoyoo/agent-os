from __future__ import annotations


class RedisHotSessionStore:
    """Redis-backed HotSessionStore adapter boundary."""

    def __init__(self, url: str, client: object | None = None) -> None:
        """创建 Redis hot store；未安装 redis extra 时给出清晰错误。"""

        if client is not None:
            self._client = client
            self._url = url
            return
        try:
            import redis
        except ImportError as error:
            raise RuntimeError(
                "RedisHotSessionStore requires the optional dependency "
                "`agentos[redis]`.",
            ) from error
        self._client = redis.Redis.from_url(url)
        self._url = url

    def __getattr__(self, name: str) -> object:
        """第一版只固定构造边界，具体 Redis schema 后续实现。"""

        raise NotImplementedError(
            f"RedisHotSessionStore.{name} is not implemented in this phase",
        )
