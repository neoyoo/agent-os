from __future__ import annotations

import base64
import json

from agentos.multi.message_queue import QueueDelivery
from agentos.multi.serializers import envelope_from_dict, envelope_to_dict
from agentos.multi.types import AgentEnvelope, AgentEnvelopeType
from agentos.persistence import BackendUnavailableError


_ENVELOPE_TYPES: tuple[AgentEnvelopeType, ...] = (
    "task_request",
    "task_result",
    "team_message",
)


class RedisAgentMessageQueueConsumerScopeError(PermissionError):
    """Raised when a Redis queue consumer accesses an agent outside its scope."""


class RedisAgentMessageQueue:
    """Redis Streams-backed AgentMessageQueue adapter."""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
        group_name: str = "agentos-workers",
        consumer_name: str = "agentos-worker",
        max_stream_length: int = 10_000,
        allowed_consumer_agent_ids: tuple[str, ...] | None = None,
        allow_unscoped_consumers: bool = False,
    ) -> None:
        """Create a Redis queue adapter."""

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
        self._allowed_consumer_agent_ids = (
            None
            if allowed_consumer_agent_ids is None
            else frozenset(str(agent_id) for agent_id in allowed_consumer_agent_ids)
        )
        self._allow_unscoped_consumers = bool(allow_unscoped_consumers)
        self._buffered_deliveries: dict[str, list[QueueDelivery]] = {}
        self._team_stream_keys_by_agent: dict[str, set[str]] = {}

    @property
    def backend_url(self) -> str:
        """Return the Redis backend URL."""

        return self._url

    def create_inbox(self, agent_id: str) -> None:
        """Create consumer groups for all envelope-type inbox streams."""

        for stream_key in self._stream_keys(agent_id):
            self._ensure_group(stream_key)

    def remove_inbox(self, agent_id: str) -> None:
        """Keep streams so pending deliveries are not lost."""

    def send(self, envelope: AgentEnvelope) -> str:
        """Write one envelope to its type-specific Redis stream."""

        payload = json.dumps(
            envelope_to_dict(envelope),
            ensure_ascii=False,
            allow_nan=False,
        )
        stream_key = self._stream_key_for_envelope(envelope)
        if envelope.type == "team_message":
            self._remember_team_stream(envelope.to_agent_id, stream_key)
            self._ensure_group(stream_key)
        return str(
            self._redis_call(
                self._client.xadd,
                stream_key,
                {"payload": payload},
                maxlen=self._max_stream_length,
                approximate=True,
            ),
        )

    def collect(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> list[QueueDelivery]:
        """Collect currently deliverable messages without draining other types."""

        self._require_consumer_scope(agent_id)
        deliveries: list[QueueDelivery] = []
        for stream_key in self._stream_keys(agent_id, envelope_types=envelope_types):
            deliveries.extend(self._buffered_deliveries.pop(stream_key, []))
            raw_streams = self._redis_call(
                self._client.xreadgroup,
                self._group_name,
                self._consumer_name,
                {stream_key: ">"},
                count=100,
                block=1,
            )
            for stream_name, messages in raw_streams:
                deliveries.extend(
                    self._deliveries_from_messages(
                        self._stream_name(stream_name),
                        messages,
                    ),
                )
        return deliveries

    def wait(self, agent_id: str, timeout: float | None = None) -> bool:
        """Wait for any envelope type and buffer one delivery for collect()."""

        return self.wait_matching(
            agent_id,
            *_ENVELOPE_TYPES,
            timeout=timeout,
        )

    def wait_matching(
        self,
        agent_id: str,
        *envelope_types: AgentEnvelopeType,
        timeout: float | None = None,
    ) -> bool:
        """Wait for one of the selected envelope types."""

        self._require_consumer_scope(agent_id)
        stream_keys = self._stream_keys(agent_id, envelope_types=envelope_types)
        for stream_key in stream_keys:
            if self._buffered_deliveries.get(stream_key):
                return True
        raw_streams = self._redis_call(
            self._client.xreadgroup,
            self._group_name,
            self._consumer_name,
            {stream_key: ">" for stream_key in stream_keys},
            count=1,
            block=self._wait_block_ms(timeout),
        )
        for stream_name, messages in raw_streams:
            buffered: list[QueueDelivery] = []
            stream_key = self._stream_name(stream_name)
            buffered.extend(self._deliveries_from_messages(stream_key, messages))
            if buffered:
                self._buffered_deliveries.setdefault(stream_key, []).extend(buffered)
                return True
        return False

    def _wait_block_ms(self, timeout: float | None) -> int | None:
        if timeout is None:
            return 0
        if timeout <= 0:
            return None
        return max(1, int(timeout * 1000))

    def collect_matching(
        self,
        agent_id: str,
        *envelope_types: AgentEnvelopeType,
    ) -> list[QueueDelivery]:
        """Collect selected envelope types."""

        return self.collect(agent_id, envelope_types=tuple(envelope_types))

    def collect_team_messages(
        self,
        agent_id: str,
        *,
        team_id: str,
    ) -> list[QueueDelivery]:
        """Collect team-message deliveries from one team-scoped stream."""

        self._require_consumer_scope(agent_id)
        stream_key = self._team_message_stream_key(agent_id, team_id)
        self._remember_team_stream(agent_id, stream_key)
        self._ensure_group(stream_key)
        deliveries = self._buffered_deliveries.pop(stream_key, [])
        raw_streams = self._redis_call(
            self._client.xreadgroup,
            self._group_name,
            self._consumer_name,
            {stream_key: ">"},
            count=100,
            block=1,
        )
        for stream_name, messages in raw_streams:
            deliveries.extend(
                self._deliveries_from_messages(
                    self._stream_name(stream_name),
                    messages,
                ),
            )
        return deliveries

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        """Ack a delivery using stream identity when the id carries it."""

        self._require_consumer_scope(agent_id)
        parsed = self._parse_delivery_id(delivery_id)
        if parsed is not None:
            stream_key, message_id = parsed
            if not self._stream_key_belongs_to_agent(agent_id, stream_key):
                return False
            return self._ack_stream(stream_key, message_id)
        acknowledged = False
        for stream_key in self._stream_keys(agent_id):
            acknowledged = self._ack_stream(stream_key, delivery_id) or acknowledged
        return acknowledged

    def requeue(self, agent_id: str, delivery: QueueDelivery) -> None:
        """Keep an unacked Redis stream delivery pending for later reclaim."""

        self._require_consumer_scope(agent_id)
        parsed = self._parse_delivery_id(delivery.delivery_id)
        if parsed is not None:
            stream_key, _message_id = parsed
            if not self._stream_key_belongs_to_agent(agent_id, stream_key):
                raise RedisAgentMessageQueueConsumerScopeError(
                    f"redis delivery does not belong to agent inbox: {agent_id}",
                )

    def ack_matching(
        self,
        agent_id: str,
        delivery_id: str,
        *envelope_types: AgentEnvelopeType,
    ) -> bool:
        """Ack a delivery from selected type streams."""

        self._require_consumer_scope(agent_id)
        parsed = self._parse_delivery_id(delivery_id)
        if parsed is not None:
            stream_key, message_id = parsed
            if not self._stream_key_belongs_to_agent(
                agent_id,
                stream_key,
                envelope_types=envelope_types,
            ):
                return False
            return self._ack_stream(stream_key, message_id)
        acknowledged = False
        for stream_key in self._stream_keys(agent_id, envelope_types=envelope_types):
            acknowledged = self._ack_stream(stream_key, delivery_id) or acknowledged
        return acknowledged

    def reclaim_pending(
        self,
        agent_id: str,
        *,
        idle_threshold_ms: int,
        max_retries: int,
        count: int = 100,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> list[QueueDelivery]:
        """Reclaim idle pending messages from selected type streams."""

        self._require_consumer_scope(agent_id)
        deliveries: list[QueueDelivery] = []
        for stream_key in self._stream_keys(agent_id, envelope_types=envelope_types):
            deliveries.extend(
                self._reclaim_pending_stream(
                    agent_id,
                    stream_key,
                    idle_threshold_ms=idle_threshold_ms,
                    max_retries=max_retries,
                    count=count,
                ),
            )
        return deliveries

    def _reclaim_pending_stream(
        self,
        agent_id: str,
        stream_key: str,
        *,
        idle_threshold_ms: int,
        max_retries: int,
        count: int,
    ) -> list[QueueDelivery]:
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
                self._ack_stream(stream_key, message_id)
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
        return self._deliveries_from_messages(stream_key, claimed)

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
                    {
                        "pending": {
                            key: self._json_safe_pending_value(value)
                            for key, value in item.items()
                        },
                    },
                    ensure_ascii=False,
                ),
            },
            maxlen=self._max_stream_length,
            approximate=True,
        )

    def _ack_stream(self, stream_key: str, delivery_id: str) -> bool:
        return bool(
            self._redis_call(
                self._client.xack,
                stream_key,
                self._group_name,
                delivery_id,
            ),
        )

    def _redis_call(self, func: object, *args: object, **kwargs: object) -> object:
        if not callable(func):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return func(*args, **kwargs)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _ensure_group(self, stream_key: str) -> None:
        try:
            self._client.xgroup_create(
                stream_key,
                self._group_name,
                id="0",
                mkstream=True,
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise

    def _require_consumer_scope(self, agent_id: str) -> None:
        if self._allowed_consumer_agent_ids is None:
            if self._allow_unscoped_consumers:
                return
            raise RedisAgentMessageQueueConsumerScopeError(
                "redis queue consumer scope must be configured with "
                "allowed_consumer_agent_ids or explicitly disabled with "
                "allow_unscoped_consumers=True",
            )
        if agent_id not in self._allowed_consumer_agent_ids:
            raise RedisAgentMessageQueueConsumerScopeError(
                f"redis queue consumer is not allowed to access agent inbox: {agent_id}",
            )

    def _stream_key_for_envelope(self, envelope: AgentEnvelope) -> str:
        if envelope.type == "team_message":
            team_id = getattr(envelope.payload, "team_id", None)
            if isinstance(team_id, str) and team_id:
                return self._team_message_stream_key(envelope.to_agent_id, team_id)
        return self._stream_key(envelope.to_agent_id, envelope_type=envelope.type)

    def _team_message_stream_key(self, agent_id: str, team_id: str) -> str:
        return f"{self._stream_key(agent_id, envelope_type='team_message')}:{team_id}"

    def _remember_team_stream(self, agent_id: str, stream_key: str) -> None:
        self._team_stream_keys_by_agent.setdefault(agent_id, set()).add(stream_key)

    def _stream_key_belongs_to_agent(
        self,
        agent_id: str,
        stream_key: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> bool:
        if stream_key in self._stream_keys(
            agent_id,
            envelope_types=envelope_types,
        ):
            return True
        if envelope_types is not None and "team_message" not in envelope_types:
            return False
        prefix = f"{self._stream_key(agent_id, envelope_type='team_message')}:"
        return stream_key.startswith(prefix)

    def _stream_keys(
        self,
        agent_id: str,
        *,
        envelope_types: tuple[AgentEnvelopeType, ...] | None = None,
    ) -> tuple[str, ...]:
        selected_types = _ENVELOPE_TYPES if envelope_types is None else envelope_types
        stream_keys = [
            self._stream_key(agent_id, envelope_type=envelope_type)
            for envelope_type in selected_types
        ]
        if "team_message" in selected_types:
            stream_keys.extend(sorted(self._team_stream_keys_by_agent.get(agent_id, ())))
        return tuple(stream_keys)

    def _stream_key(
        self,
        agent_id: str,
        *,
        envelope_type: AgentEnvelopeType,
    ) -> str:
        base = f"{self._key_prefix}:multi:inbox:{agent_id}"
        if envelope_type == "task_request":
            return base
        return f"{base}:{envelope_type}"

    def _deliveries_from_messages(
        self,
        stream_key: str,
        messages: list[tuple[object, dict[str, object]]],
    ) -> list[QueueDelivery]:
        deliveries: list[QueueDelivery] = []
        for message_id, fields in messages:
            raw_message_id = self._message_id(message_id)
            payload = self._payload_field(fields)
            deliveries.append(
                QueueDelivery(
                    delivery_id=self._delivery_id(stream_key, raw_message_id),
                    envelope=envelope_from_dict(json.loads(str(payload))),
                ),
            )
        return deliveries

    def _delivery_id(self, stream_key: str, message_id: str) -> str:
        payload = json.dumps(
            {"stream": stream_key, "message_id": message_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return f"redis-stream:{encoded}"

    def _parse_delivery_id(self, delivery_id: str) -> tuple[str, str] | None:
        prefix = "redis-stream:"
        if not delivery_id.startswith(prefix):
            return None
        encoded = delivery_id.removeprefix(prefix)
        padding = "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"),
            )
        except Exception:
            return None
        stream_key = payload.get("stream")
        message_id = payload.get("message_id")
        if not isinstance(stream_key, str) or not isinstance(message_id, str):
            return None
        return stream_key, message_id

    def _message_id(self, value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _stream_name(self, value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _payload_field(self, fields: dict[object, object]) -> str:
        payload = fields.get("payload")
        if payload is None:
            payload = fields.get(b"payload")
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        if isinstance(payload, str):
            return payload
        raise BackendUnavailableError("Redis stream message is missing payload")

    def _json_safe_pending_value(self, value: object) -> object:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
