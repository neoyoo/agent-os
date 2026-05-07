from __future__ import annotations

import json
from typing import Sequence

from agentos.memory.serializers import (
    hot_state_from_dict,
    hot_state_to_dict,
    message_from_dict,
    message_to_dict,
)
from agentos.memory.types import HotSessionState
from agentos.messages import Message


class RedisHotSessionStore:
    """Redis-backed HotSessionStore adapter。"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
        ttl_seconds: int | None = None,
    ) -> None:
        """创建 Redis hot store；未安装 redis extra 时给出清晰错误。"""

        if client is not None:
            self._client = client
            self._url = url
            self._key_prefix = key_prefix.rstrip(":")
            self._ttl_seconds = ttl_seconds
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
        self._key_prefix = key_prefix.rstrip(":")
        self._ttl_seconds = ttl_seconds

    def load_hot_state(self, session_id: str) -> HotSessionState | None:
        """读取热点 session state；未命中返回 None。"""

        pipeline = self._client.pipeline()
        pipeline.get(self._state_key(session_id))
        pipeline.hgetall(self._segment_refs_key(session_id))
        pipeline.get(self._temporary_refs_key(session_id))
        raw, raw_segment_refs, raw_temporary_refs = pipeline.execute()
        if raw is None:
            return None

        state = hot_state_from_dict(json.loads(self._decode(raw)))
        segment_refs = self._segment_refs_from_raw(raw_segment_refs)
        temporary_refs = self._temporary_refs_from_raw(raw_temporary_refs)
        self._refresh_ttl(session_id)
        return HotSessionState(
            session_id=state.session_id,
            active_refs=state.active_refs,
            recent_messages=state.recent_messages,
            temporary_recalled_refs=temporary_refs,
            segment_refs=segment_refs or state.segment_refs,
            metadata=state.metadata,
        )

    def save_hot_state(self, state: HotSessionState) -> None:
        """保存热点 session state，并同步其中携带的消息和 refs。"""

        pipeline = self._client.pipeline()
        pipeline.set(
            self._state_key(state.session_id),
            json.dumps(hot_state_to_dict(state), ensure_ascii=False),
            ex=self._ttl_seconds,
        )
        for message in state.recent_messages:
            pipeline.hset(
                self._messages_key(state.session_id),
                message.id,
                json.dumps(message_to_dict(message), ensure_ascii=False),
            )
        for segment_id, refs in state.segment_refs.items():
            pipeline.hset(
                self._segment_refs_key(state.session_id),
                segment_id,
                json.dumps(list(refs), ensure_ascii=False),
            )
        pipeline.set(
            self._temporary_refs_key(state.session_id),
            json.dumps(list(state.temporary_recalled_refs), ensure_ascii=False),
            ex=self._ttl_seconds,
        )
        self._queue_ttl_refresh(pipeline, state.session_id)
        pipeline.execute()

    def append_hot_message(self, session_id: str, message: Message) -> None:
        """追加一条热点原文消息。"""

        self._client.hset(
            self._messages_key(session_id),
            message.id,
            json.dumps(message_to_dict(message), ensure_ascii=False),
        )
        self._refresh_ttl(session_id)

    def get_hot_messages(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> list[Message] | None:
        """按 ids 读取热点消息；任一缺失返回 None。"""

        raw_messages = self._client.hmget(
            self._messages_key(session_id),
            list(message_ids),
        )
        if any(raw_message is None for raw_message in raw_messages):
            return None
        self._refresh_ttl(session_id)
        return [
            message_from_dict(json.loads(self._decode(raw_message)))
            for raw_message in raw_messages
            if raw_message is not None
        ]

    def save_segment_refs(
        self,
        session_id: str,
        segment_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """保存热点 segment refs。"""

        self._client.hset(
            self._segment_refs_key(session_id),
            segment_id,
            json.dumps(list(message_ids), ensure_ascii=False),
        )
        self._refresh_ttl(session_id)

    def get_segment_refs(
        self,
        session_id: str,
        segment_id: str,
    ) -> tuple[str, ...] | None:
        """读取热点 segment refs；未命中返回 None。"""

        raw = self._client.hget(self._segment_refs_key(session_id), segment_id)
        if raw is None:
            return None
        self._refresh_ttl(session_id)
        return tuple(str(ref) for ref in json.loads(self._decode(raw)))

    def set_temporary_recalled_refs(
        self,
        session_id: str,
        message_ids: Sequence[str],
    ) -> None:
        """设置一次性 recalled refs。"""

        self._client.set(
            self._temporary_refs_key(session_id),
            json.dumps(list(message_ids), ensure_ascii=False),
            ex=self._ttl_seconds,
        )

    def consume_temporary_recalled_refs(self, session_id: str) -> tuple[str, ...]:
        """消费并清空一次性 recalled refs。"""

        key = self._temporary_refs_key(session_id)
        raw = self._atomic_getdel(key)
        if raw is None:
            return ()
        self._refresh_ttl(session_id)
        return tuple(str(ref) for ref in json.loads(self._decode(raw)))

    def _segment_refs_from_raw(self, raw_refs: object) -> dict[str, tuple[str, ...]]:
        """从 Redis hash 结果恢复热点 segment refs。"""

        return {
            self._decode(segment_id): tuple(
                str(ref) for ref in json.loads(self._decode(refs))
            )
            for segment_id, refs in dict(raw_refs).items()
        }

    def _temporary_refs_from_raw(self, raw: object | None) -> tuple[str, ...]:
        """从 Redis value 结果恢复 temporary recalled refs。"""

        if raw is None:
            return ()
        return tuple(str(ref) for ref in json.loads(self._decode(raw)))

    def _refresh_ttl(self, session_id: str) -> None:
        """刷新 session-scoped keys 的滑动 TTL。"""

        if self._ttl_seconds is None:
            return
        pipeline = self._client.pipeline()
        self._queue_ttl_refresh(pipeline, session_id)
        pipeline.execute()

    def _queue_ttl_refresh(self, pipeline: object, session_id: str) -> None:
        """把 session-scoped TTL 刷新命令加入 pipeline。"""

        if self._ttl_seconds is None:
            return
        for key in [
            self._state_key(session_id),
            self._messages_key(session_id),
            self._segment_refs_key(session_id),
            self._temporary_refs_key(session_id),
        ]:
            pipeline.expire(key, self._ttl_seconds)

    def _atomic_getdel(self, key: str) -> object | None:
        """原子读取并删除一次性 key。"""

        getdel = getattr(self._client, "getdel", None)
        if callable(getdel):
            return getdel(key)

        eval_script = getattr(self._client, "eval", None)
        if callable(eval_script):
            return eval_script(
                """
                local value = redis.call("GET", KEYS[1])
                if value then
                    redis.call("DEL", KEYS[1])
                end
                return value
                """,
                1,
                key,
            )

        raise RuntimeError(
            "RedisHotSessionStore requires Redis GETDEL or Lua EVAL "
            "for atomic consume",
        )

    def _state_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:hot:{session_id}:state"

    def _messages_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:hot:{session_id}:messages"

    def _segment_refs_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:hot:{session_id}:segment_refs"

    def _temporary_refs_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:hot:{session_id}:temporary_recalled_refs"

    def _decode(self, value: object) -> str:
        """兼容 redis-py bytes 返回值和测试 fake 的 str 返回值。"""

        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
