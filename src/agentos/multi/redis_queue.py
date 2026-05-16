from __future__ import annotations

import json

from agentos.multi.message_queue import QueueDelivery
from agentos.multi.serializers import envelope_from_dict, envelope_to_dict
from agentos.multi.types import AgentEnvelope
from agentos.persistence import BackendUnavailableError


class RedisAgentMessageQueue:
    """Redis Streams-backed AgentMessageQueue adapter。"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
        group_name: str = "agentos-workers",
        consumer_name: str = "agentos-worker",
        max_stream_length: int = 10_000,
    ) -> None:
        """创建 Redis queue；未安装 redis extra 时给出清晰错误。"""

        if client is not None:
            self._client = client
            self._url = url
        else:
            try:
                import redis
            except ImportError as error:
                raise RuntimeError(
                    "RedisAgentMessageQueue requires the optional dependency "
                    "`agentos[redis]`.",
                ) from error
            self._client = redis.Redis.from_url(url)
            self._url = url
        self._key_prefix = key_prefix.rstrip(":")
        self._group_name = group_name
        self._consumer_name = consumer_name
        self._max_stream_length = max_stream_length
        self._buffered_deliveries: dict[str, list[QueueDelivery]] = {}

    @property
    def backend_url(self) -> str:
        """返回 Redis backend URL。"""

        return self._url

    def create_inbox(self, agent_id: str) -> None:
        """创建 stream consumer group；已存在时保持幂等。"""

        try:
            self._client.xgroup_create(
                self._stream_key(agent_id),
                self._group_name,
                id="0",
                mkstream=True,
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise

    def remove_inbox(self, agent_id: str) -> None:
        """第一版不删除 stream，避免误删 pending delivery。"""

    def send(self, envelope: AgentEnvelope) -> str:
        """写入目标 agent stream，并返回 Redis stream message id。"""

        return str(
            self._redis_call(
                self._client.xadd,
                self._stream_key(envelope.to_agent_id),
                {
                    "payload": json.dumps(
                        envelope_to_dict(envelope),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                },
                maxlen=self._max_stream_length,
                approximate=True,
            ),
        )

    def collect(self, agent_id: str) -> list[QueueDelivery]:
        """读取当前可处理 deliveries。"""

        deliveries = self._buffered_deliveries.pop(agent_id, [])
        raw_streams = self._redis_call(
            self._client.xreadgroup,
            self._group_name,
            self._consumer_name,
            {self._stream_key(agent_id): ">"},
            count=100,
            block=1,
        )
        for _stream_name, messages in raw_streams:
            for message_id, fields in messages:
                payload = fields.get("payload")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")
                deliveries.append(
                    QueueDelivery(
                        delivery_id=self._message_id(message_id),
                        envelope=envelope_from_dict(json.loads(str(payload))),
                    ),
                )
        return deliveries

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """等待 stream 中出现消息，并缓存预取 delivery 供 collect 使用。"""

        if self._buffered_deliveries.get(agent_id):
            return True
        raw_streams = self._redis_call(
            self._client.xreadgroup,
            self._group_name,
            self._consumer_name,
            {self._stream_key(agent_id): ">"},
            count=1,
            block=None if timeout is None else max(0, int(timeout * 1000)),
        )
        buffered: list[QueueDelivery] = []
        for _stream_name, messages in raw_streams:
            for message_id, fields in messages:
                payload = fields.get("payload")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")
                buffered.append(
                    QueueDelivery(
                        delivery_id=self._message_id(message_id),
                        envelope=envelope_from_dict(json.loads(str(payload))),
                    ),
                )
        if buffered:
            self._buffered_deliveries.setdefault(agent_id, []).extend(buffered)
            return True
        return False

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """确认 stream message 已处理。"""

        return bool(
            self._redis_call(
                self._client.xack,
                self._stream_key(agent_id),
                self._group_name,
                delivery_id,
            ),
        )

    def reclaim_pending(
        self,
        agent_id: str,
        *,
        idle_threshold_ms: int,
        max_retries: int,
        count: int = 100,
    ) -> list[QueueDelivery]:
        """用 XPENDING/XCLAIM 重新领取 idle pending messages。"""

        stream_key = self._stream_key(agent_id)
        pending = self._redis_call(
            self._client.xpending_range,
            stream_key,
            self._group_name,
            min="-",
            max="+",
            count=count,
        )
        claim_ids: list[str] = []
        for item in pending:
            message_id = self._message_id(item.get("message_id"))
            idle = int(item.get("time_since_delivered", 0))
            deliveries = int(item.get("times_delivered", 0))
            if idle < idle_threshold_ms:
                continue
            if deliveries > max_retries:
                self._dead_letter(stream_key, message_id, item)
                self.ack(agent_id, message_id)
                continue
            claim_ids.append(message_id)
        if not claim_ids:
            return []
        claimed = self._redis_call(
            self._client.xclaim,
            stream_key,
            self._group_name,
            self._consumer_name,
            idle_threshold_ms,
            claim_ids,
        )
        deliveries: list[QueueDelivery] = []
        for message_id, fields in claimed:
            payload = fields.get("payload")
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            deliveries.append(
                QueueDelivery(
                    delivery_id=self._message_id(message_id),
                    envelope=envelope_from_dict(json.loads(str(payload))),
                ),
            )
        return deliveries

    def _dead_letter(
        self,
        stream_key: str,
        message_id: str,
        item: dict[str, object],
    ) -> None:
        self._redis_call(
            self._client.xadd,
            f"{stream_key}:dead",
            {
                "message_id": message_id,
                "payload": json.dumps(
                    {"pending": {key: self._message_id(value) for key, value in item.items()}},
                    ensure_ascii=False,
                ),
            },
            maxlen=self._max_stream_length,
            approximate=True,
        )

    def _redis_call(self, func: object, *args: object, **kwargs: object) -> object:
        if not callable(func):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return func(*args, **kwargs)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _stream_key(self, agent_id: str) -> str:
        return f"{self._key_prefix}:multi:inbox:{agent_id}"

    def _message_id(self, value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
