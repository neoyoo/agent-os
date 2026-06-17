from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from uuid import uuid4

from agentos.persistence import SessionPersistence, SessionSnapshot, SessionSnapshotRecord
from agentos.persistence import BackendUnavailableError
from agentos.runtime import Agent


class SessionLeaseError(RuntimeError):
    """Raised when a session lease cannot be acquired or refreshed."""


@dataclass(frozen=True, slots=True)
class SessionLease:
    """Exclusive ownership token for one session."""

    session_id: str
    owner_id: str
    token: str
    expires_at: float | None = None


class SessionLeaseStore(Protocol):
    """Exclusive lease backend for session mutation."""

    def acquire(
        self,
        session_id: str,
        *,
        owner_id: str,
        ttl_seconds: float,
        wait_timeout_seconds: float | None = None,
    ) -> SessionLease:
        """Acquire exclusive ownership for one session."""

    def release(self, lease: SessionLease) -> None:
        """Release a lease if still owned by the token."""

    def refresh(self, lease: SessionLease) -> SessionLease:
        """Extend a lease if still owned by the token."""

    def ensure_owned(self, lease: SessionLease) -> None:
        """Raise when the lease token no longer owns the session."""


class InMemorySessionLeaseStore:
    """In-process lease store for tests and single-node development."""

    def __init__(self) -> None:
        """Create an empty in-memory lease store."""

        self._leases: dict[str, SessionLease] = {}
        self._lease_ttls: dict[str, float] = {}
        self._lock = RLock()

    def acquire(
        self,
        session_id: str,
        *,
        owner_id: str,
        ttl_seconds: float,
        wait_timeout_seconds: float | None = None,
    ) -> SessionLease:
        """Acquire a session lease, waiting up to the configured timeout."""

        deadline = (
            None
            if wait_timeout_seconds is None
            else time.monotonic() + wait_timeout_seconds
        )
        while True:
            with self._lock:
                self._drop_expired_locked(session_id)
                if session_id not in self._leases:
                    lease = SessionLease(
                        session_id=session_id,
                        owner_id=owner_id,
                        token=uuid4().hex,
                        expires_at=time.monotonic() + ttl_seconds,
                    )
                    self._leases[session_id] = lease
                    self._lease_ttls[session_id] = ttl_seconds
                    return lease
            if wait_timeout_seconds == 0:
                raise SessionLeaseError(f"session is locked: {session_id}")
            if deadline is not None and time.monotonic() >= deadline:
                raise SessionLeaseError(f"session is locked: {session_id}")
            time.sleep(0.01)

    def release(self, lease: SessionLease) -> None:
        """Release a lease and ignore stale tokens."""

        with self._lock:
            current = self._leases.get(lease.session_id)
            if current is not None and current.token == lease.token:
                del self._leases[lease.session_id]
                self._lease_ttls.pop(lease.session_id, None)

    def refresh(self, lease: SessionLease) -> SessionLease:
        """Refresh a currently owned lease."""

        with self._lock:
            current = self._leases.get(lease.session_id)
            if current is None or current.token != lease.token:
                raise SessionLeaseError(
                    f"session lease is not owned: {lease.session_id}",
                )
            ttl_seconds = self._lease_ttls.get(lease.session_id)
            if ttl_seconds is None:
                ttl_seconds = (
                    0.0
                    if current.expires_at is None
                    else max(0.0, current.expires_at - time.monotonic())
                )
            refreshed = SessionLease(
                session_id=current.session_id,
                owner_id=current.owner_id,
                token=current.token,
                expires_at=time.monotonic() + ttl_seconds,
            )
            self._leases[lease.session_id] = refreshed
            return refreshed

    def ensure_owned(self, lease: SessionLease) -> None:
        """Confirm the lease token still owns the in-memory session lock."""

        with self._lock:
            self._drop_expired_locked(lease.session_id)
            current = self._leases.get(lease.session_id)
            if current is None or current.token != lease.token:
                raise SessionLeaseError(
                    f"session lease is not owned: {lease.session_id}",
                )

    def _drop_expired_locked(self, session_id: str) -> None:
        current = self._leases.get(session_id)
        if current is not None and current.expires_at is not None:
            if current.expires_at <= time.monotonic():
                del self._leases[session_id]
                self._lease_ttls.pop(session_id, None)


class RedisSessionLeaseStore:
    """Redis-backed exclusive lease store for distributed web sessions."""

    _RELEASE_IF_TOKEN_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local payload = cjson.decode(current)
if payload['token'] ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""
    _REFRESH_IF_TOKEN_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  return 0
end
local payload = cjson.decode(current)
if payload['token'] ~= ARGV[1] then
  return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ARGV[3])
return 1
"""

    def __init__(
        self,
        url: str,
        client: object | None = None,
        *,
        key_prefix: str = "agentos",
    ) -> None:
        """Create a Redis lease store; Redis dependency is optional."""

        if client is not None:
            self._client = client
            self._url = url
        else:
            try:
                import redis
            except ImportError as error:
                raise RuntimeError(
                    "RedisSessionLeaseStore requires the optional dependency "
                    "`agentos[redis]`.",
                ) from error
            self._client = redis.Redis.from_url(url)
            self._url = url
        self._key_prefix = key_prefix.rstrip(":")
        self._lease_ttls: dict[str, float] = {}

    @property
    def backend_url(self) -> str:
        """Return the Redis backend URL."""

        return self._url

    def acquire(
        self,
        session_id: str,
        *,
        owner_id: str,
        ttl_seconds: float,
        wait_timeout_seconds: float | None = None,
    ) -> SessionLease:
        """Acquire a Redis-backed session lease."""

        deadline = (
            None
            if wait_timeout_seconds is None
            else time.monotonic() + wait_timeout_seconds
        )
        while True:
            token = uuid4().hex
            expires_at = time.monotonic() + ttl_seconds
            payload = self._payload(
                owner_id=owner_id,
                token=token,
                expires_at=expires_at,
            )
            if self._redis_set(
                self._key(session_id),
                payload,
                nx=True,
                px=self._ttl_ms(ttl_seconds),
            ):
                self._lease_ttls[token] = ttl_seconds
                return SessionLease(
                    session_id=session_id,
                    owner_id=owner_id,
                    token=token,
                    expires_at=expires_at,
                )
            if wait_timeout_seconds == 0:
                raise SessionLeaseError(f"session is locked: {session_id}")
            if deadline is not None and time.monotonic() >= deadline:
                raise SessionLeaseError(f"session is locked: {session_id}")
            time.sleep(0.01)

    def release(self, lease: SessionLease) -> None:
        """Release a Redis lease if the token still owns it."""

        key = self._key(lease.session_id)
        self._redis_eval(
            self._RELEASE_IF_TOKEN_SCRIPT,
            1,
            key,
            lease.token,
        )
        self._lease_ttls.pop(lease.token, None)

    def refresh(self, lease: SessionLease) -> SessionLease:
        """Refresh a Redis lease when the token still owns it."""

        key = self._key(lease.session_id)
        ttl_seconds = self._lease_ttls.get(lease.token)
        if ttl_seconds is None:
            ttl_seconds = (
                0.0
                if lease.expires_at is None
                else max(0.0, lease.expires_at - time.monotonic())
            )
        expires_at = time.monotonic() + ttl_seconds
        payload = self._payload(
            owner_id=lease.owner_id,
            token=lease.token,
            expires_at=expires_at,
        )
        if not self._redis_eval(
            self._REFRESH_IF_TOKEN_SCRIPT,
            1,
            key,
            lease.token,
            payload,
            self._ttl_ms(ttl_seconds),
        ):
            raise SessionLeaseError(
                f"session lease is not owned: {lease.session_id}",
            )
        return SessionLease(
            session_id=lease.session_id,
            owner_id=lease.owner_id,
            token=lease.token,
            expires_at=expires_at,
        )

    def ensure_owned(self, lease: SessionLease) -> None:
        """Confirm the Redis lease key is still owned by the lease token."""

        payload = self._current_payload(self._key(lease.session_id))
        if payload is None or payload.get("token") != lease.token:
            raise SessionLeaseError(
                f"session lease is not owned: {lease.session_id}",
            )

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}:session:lease:{session_id}"

    def _payload(self, *, owner_id: str, token: str, expires_at: float) -> str:
        return json.dumps(
            {
                "owner_id": owner_id,
                "token": token,
                "expires_at": expires_at,
            },
            ensure_ascii=False,
            allow_nan=False,
        )

    def _current_payload(self, key: str) -> dict[str, object] | None:
        raw = self._redis_get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(str(raw))

    def _ttl_ms(self, ttl_seconds: float) -> int:
        return max(1, int(ttl_seconds * 1000))

    def _redis_set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        px: int,
    ) -> bool:
        set_method = getattr(self._client, "set", None)
        if not callable(set_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return bool(set_method(key, value, nx=nx, px=px))
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

    def _redis_delete(self, key: str) -> None:
        delete_method = getattr(self._client, "delete", None)
        if not callable(delete_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            delete_method(key)
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error

    def _redis_eval(
        self,
        script: str,
        numkeys: int,
        *args: object,
    ) -> bool:
        eval_method = getattr(self._client, "eval", None)
        if not callable(eval_method):
            raise BackendUnavailableError("Redis backend unavailable")
        try:
            return bool(eval_method(script, numkeys, *args))
        except Exception as error:
            raise BackendUnavailableError("Redis backend unavailable") from error


class SnapshotAgentFactory(Protocol):
    """Build agents from snapshots and extract snapshots from agents."""

    def create_agent(
        self,
        *,
        session_id: str,
        snapshot: SessionSnapshot | None,
    ) -> Agent:
        """Build an agent from an optional snapshot."""

    def create_snapshot(self, *, session_id: str, agent: Agent) -> SessionSnapshot:
        """Extract a snapshot from an agent."""


class CompareAndSaveSessionPersistence(SessionPersistence, Protocol):
    """Optional persistence capability for generation-guarded snapshot writes."""

    def load_record(self, session_id: str) -> SessionSnapshotRecord:
        """Load a snapshot and its backend mutation revision."""

    def save_if_unchanged(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
    ) -> SessionSnapshotRecord:
        """Save snapshot only when backend revision is unchanged."""


class DurableAgentSessionProvider:
    """Session provider with lock, hydrate, save, and release lifecycle."""

    def __init__(
        self,
        *,
        agent_factory: SnapshotAgentFactory,
        persistence: SessionPersistence,
        lease_store: SessionLeaseStore,
        owner_id: str,
        lease_ttl_seconds: float = 60.0,
        acquire_timeout_seconds: float | None = 10.0,
    ) -> None:
        """Create a durable session provider."""

        self._agent_factory = agent_factory
        self._persistence = persistence
        self._lease_store = lease_store
        self._owner_id = owner_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._active_leases: dict[str, SessionLease] = {}
        self._active_revisions: dict[str, int] = {}
        self._active_lock = RLock()

    def get_agent(self, session_id: str) -> Agent:
        """Acquire the lease and hydrate an agent for the session."""

        with self._active_lock:
            if session_id in self._active_leases:
                raise SessionLeaseError(f"session is already active: {session_id}")
        lease = self._lease_store.acquire(
            session_id,
            owner_id=self._owner_id,
            ttl_seconds=self._lease_ttl_seconds,
            wait_timeout_seconds=self._acquire_timeout_seconds,
        )
        try:
            try:
                record = self._load_record_if_supported(session_id)
                snapshot = record.snapshot
                revision = record.revision
            except KeyError:
                snapshot = None
                revision = 0
            agent = self._agent_factory.create_agent(
                session_id=session_id,
                snapshot=snapshot,
            )
        except Exception:
            self._lease_store.release(lease)
            raise
        with self._active_lock:
            self._active_leases[session_id] = lease
            self._active_revisions[session_id] = revision
        return agent

    def release_agent(self, session_id: str, agent: Agent) -> None:
        """Save the session snapshot and release the lease."""

        with self._active_lock:
            lease = self._active_leases.pop(session_id, None)
            revision = self._active_revisions.pop(session_id, 0)
        if lease is None:
            raise SessionLeaseError(f"session is not active: {session_id}")
        try:
            snapshot = self._agent_factory.create_snapshot(
                session_id=session_id,
                agent=agent,
            )
            self._ensure_lease_owned(lease)
            self._save_snapshot(snapshot, expected_revision=revision)
        finally:
            self._lease_store.release(lease)

    def abandon_agent(self, session_id: str, agent: Agent) -> None:
        """Release the active lease without saving a possibly stale snapshot."""

        with self._active_lock:
            lease = self._active_leases.pop(session_id, None)
            self._active_revisions.pop(session_id, None)
        if lease is None:
            raise SessionLeaseError(f"session is not active: {session_id}")
        self._lease_store.release(lease)

    def refresh_agent(self, session_id: str) -> SessionLease:
        """Refresh the active session lease during a long-running turn."""

        with self._active_lock:
            lease = self._active_leases.get(session_id)
        if lease is None:
            raise SessionLeaseError(f"session is not active: {session_id}")
        refreshed = self._lease_store.refresh(lease)
        with self._active_lock:
            if self._active_leases.get(session_id) is lease:
                self._active_leases[session_id] = refreshed
            else:
                current = self._active_leases.get(session_id)
                if current is None or current.token != refreshed.token:
                    raise SessionLeaseError(f"session is not active: {session_id}")
                self._active_leases[session_id] = refreshed
        return refreshed

    async def async_get_agent(self, session_id: str) -> Agent:
        """Run synchronous hydration in a worker thread for async channels."""

        return await asyncio.to_thread(self.get_agent, session_id)

    async def async_release_agent(self, session_id: str, agent: Agent) -> None:
        """Run synchronous save/release in a worker thread for async channels."""

        await asyncio.to_thread(self.release_agent, session_id, agent)

    async def async_abandon_agent(self, session_id: str, agent: Agent) -> None:
        """Run synchronous no-save release in a worker thread."""

        await asyncio.to_thread(self.abandon_agent, session_id, agent)

    async def async_refresh_agent(self, session_id: str) -> SessionLease:
        """Run synchronous lease refresh in a worker thread for async channels."""

        return await asyncio.to_thread(self.refresh_agent, session_id)

    def shutdown(self) -> None:
        """Release leases still held by this provider."""

        with self._active_lock:
            leases = list(self._active_leases.values())
            self._active_leases.clear()
            self._active_revisions.clear()
        for lease in leases:
            self._lease_store.release(lease)

    def _load_record_if_supported(self, session_id: str) -> SessionSnapshotRecord:
        load_record = getattr(self._persistence, "load_record", None)
        if callable(load_record):
            return load_record(session_id)
        return SessionSnapshotRecord(
            snapshot=self._persistence.load(session_id),
            revision=0,
        )

    def _save_snapshot(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
    ) -> None:
        save_if_unchanged = getattr(self._persistence, "save_if_unchanged", None)
        if callable(save_if_unchanged):
            save_if_unchanged(snapshot, expected_revision=expected_revision)
            return
        self._persistence.save(snapshot)

    def _ensure_lease_owned(self, lease: SessionLease) -> None:
        ensure_owned = getattr(self._lease_store, "ensure_owned", None)
        if callable(ensure_owned):
            ensure_owned(lease)
            return
        self._lease_store.refresh(lease)
