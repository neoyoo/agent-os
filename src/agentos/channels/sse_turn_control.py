from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from agentos.persistence import BackendUnavailableError


class SseTurnAlreadyActiveError(RuntimeError):
    """Raised when a session already has an active streaming turn."""


@dataclass(frozen=True, slots=True)
class SseTurnControlState:
    """Shared control state for one active SSE turn."""

    session_id: str
    turn_stream_id: str
    owner_id: str
    interrupt_requested: bool = False
    expires_at: float | None = None


class SseTurnControlStore(Protocol):
    """Shared active-turn and interrupt boundary for distributed SSE turns."""

    def claim_turn(
        self,
        session_id: str,
        *,
        turn_stream_id: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> SseTurnControlState:
        """Claim the active streaming turn for a session."""

    def get_turn(self, session_id: str) -> SseTurnControlState | None:
        """Return the current active turn state, if any."""

    def request_interrupt(self, session_id: str) -> bool:
        """Mark the active turn interrupted. Return False when no turn exists."""

    def refresh_turn(self, session_id: str, turn_stream_id: str, ttl_seconds: float) -> None:
        """Refresh the active turn TTL if it still matches the turn id."""

    def release_turn(self, session_id: str, turn_stream_id: str) -> None:
        """Release the active turn if it still matches the turn id."""


class InMemorySseTurnControlStore:
    """In-process active-turn control store for tests and single-node development."""

    def __init__(self) -> None:
        self._states: dict[str, SseTurnControlState] = {}
        self._lock = RLock()

    def claim_turn(
        self,
        session_id: str,
        *,
        turn_stream_id: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> SseTurnControlState:
        with self._lock:
            self._drop_expired_locked(session_id)
            if session_id in self._states:
                raise SseTurnAlreadyActiveError(
                    f"session has active stream turn: {session_id}",
                )
            state = SseTurnControlState(
                session_id=session_id,
                turn_stream_id=turn_stream_id,
                owner_id=owner_id,
                expires_at=time.monotonic() + ttl_seconds,
            )
            self._states[session_id] = state
            return state

    def get_turn(self, session_id: str) -> SseTurnControlState | None:
        with self._lock:
            self._drop_expired_locked(session_id)
            return self._states.get(session_id)

    def request_interrupt(self, session_id: str) -> bool:
        with self._lock:
            self._drop_expired_locked(session_id)
            current = self._states.get(session_id)
            if current is None:
                return False
            self._states[session_id] = SseTurnControlState(
                session_id=current.session_id,
                turn_stream_id=current.turn_stream_id,
                owner_id=current.owner_id,
                interrupt_requested=True,
                expires_at=current.expires_at,
            )
            return True

    def refresh_turn(self, session_id: str, turn_stream_id: str, ttl_seconds: float) -> None:
        with self._lock:
            current = self._states.get(session_id)
            if current is None or current.turn_stream_id != turn_stream_id:
                return
            self._states[session_id] = SseTurnControlState(
                session_id=current.session_id,
                turn_stream_id=current.turn_stream_id,
                owner_id=current.owner_id,
                interrupt_requested=current.interrupt_requested,
                expires_at=time.monotonic() + ttl_seconds,
            )

    def release_turn(self, session_id: str, turn_stream_id: str) -> None:
        with self._lock:
            current = self._states.get(session_id)
            if current is not None and current.turn_stream_id == turn_stream_id:
                del self._states[session_id]

    def _drop_expired_locked(self, session_id: str) -> None:
        current = self._states.get(session_id)
        if current is not None and current.expires_at is not None:
            if current.expires_at <= time.monotonic():
                del self._states[session_id]


class RedisSseTurnControlStore:
    """Redis-backed active-turn control store for distributed web agents."""

    _RELEASE_IF_TURN_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local payload = cjson.decode(current)
if payload['turn_stream_id'] ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

    _INTERRUPT_IF_ACTIVE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local payload = cjson.decode(current)
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then
  ttl = tonumber(ARGV[1])
end
payload['interrupt_requested'] = true
redis.call('SET', KEYS[1], cjson.encode(payload), 'PX', ttl)
return 1
"""

    _REFRESH_IF_TURN_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local payload = cjson.decode(current)
if payload['turn_stream_id'] ~= ARGV[1] then
  return 0
end
payload['expires_at'] = tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(payload), 'PX', ARGV[3])
return 1
"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
    ) -> None:
        if client is not None:
            self._client = client
            self._url = url
        else:
            try:
                import redis
            except ImportError as error:
                raise RuntimeError(
                    "RedisSseTurnControlStore requires the optional dependency "
                    "`agentos[redis]`.",
                ) from error
            self._client = redis.Redis.from_url(url)
            self._url = url
        self._key_prefix = key_prefix.rstrip(":")
        self._ttls: dict[str, float] = {}

    @property
    def backend_url(self) -> str:
        """Return the Redis backend URL."""

        return self._url

    def claim_turn(
        self,
        session_id: str,
        *,
        turn_stream_id: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> SseTurnControlState:
        expires_at = time.monotonic() + ttl_seconds
        state = SseTurnControlState(
            session_id=session_id,
            turn_stream_id=turn_stream_id,
            owner_id=owner_id,
            expires_at=expires_at,
        )
        if not self._redis_set(
            self._key(session_id),
            self._payload(state),
            nx=True,
            px=self._ttl_ms(ttl_seconds),
        ):
            raise SseTurnAlreadyActiveError(
                f"session has active stream turn: {session_id}",
            )
        self._ttls[session_id] = ttl_seconds
        return state

    def get_turn(self, session_id: str) -> SseTurnControlState | None:
        raw = self._redis_get(self._key(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(str(raw))
        return self._state_from_payload(session_id, payload)

    def request_interrupt(self, session_id: str) -> bool:
        ttl_seconds = self._ttls.get(session_id, 60.0)
        return bool(
            self._redis_eval(
                self._INTERRUPT_IF_ACTIVE_SCRIPT,
                1,
                self._key(session_id),
                self._ttl_ms(ttl_seconds),
            ),
        )

    def refresh_turn(self, session_id: str, turn_stream_id: str, ttl_seconds: float) -> None:
        expires_at = time.monotonic() + ttl_seconds
        if self._redis_eval(
            self._REFRESH_IF_TURN_SCRIPT,
            1,
            self._key(session_id),
            turn_stream_id,
            expires_at,
            self._ttl_ms(ttl_seconds),
        ):
            self._ttls[session_id] = ttl_seconds
        else:
            self._ttls.pop(session_id, None)

    def release_turn(self, session_id: str, turn_stream_id: str) -> None:
        self._redis_eval(
            self._RELEASE_IF_TURN_SCRIPT,
            1,
            self._key(session_id),
            turn_stream_id,
        )
        self._ttls.pop(session_id, None)

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}:sse:turn:{session_id}"

    def _payload(self, state: SseTurnControlState) -> str:
        return json.dumps(
            {
                "session_id": state.session_id,
                "turn_stream_id": state.turn_stream_id,
                "owner_id": state.owner_id,
                "interrupt_requested": state.interrupt_requested,
                "expires_at": state.expires_at,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    def _state_from_payload(
        self,
        session_id: str,
        payload: dict[str, object],
    ) -> SseTurnControlState:
        return SseTurnControlState(
            session_id=str(payload.get("session_id") or session_id),
            turn_stream_id=str(payload["turn_stream_id"]),
            owner_id=str(payload["owner_id"]),
            interrupt_requested=bool(payload.get("interrupt_requested", False)),
            expires_at=(
                float(payload["expires_at"])
                if payload.get("expires_at") is not None
                else None
            ),
        )

    def _ttl_ms(self, ttl_seconds: float) -> int:
        return max(1, int(ttl_seconds * 1000))

    def _redis_set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        xx: bool = False,
        px: int,
    ) -> bool:
        set_method = getattr(self._client, "set", None)
        if not callable(set_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            kwargs: dict[str, object] = {"px": px}
            if nx:
                kwargs["nx"] = True
            if xx:
                kwargs["xx"] = True
            return bool(set_method(key, value, **kwargs))
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _redis_get(self, key: str) -> object | None:
        get_method = getattr(self._client, "get", None)
        if not callable(get_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return get_method(key)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _redis_eval(self, script: str, numkeys: int, *args: object) -> object:
        eval_method = getattr(self._client, "eval", None)
        if not callable(eval_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return eval_method(script, numkeys, *args)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error
