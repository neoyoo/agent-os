from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
import os
import subprocess

import pytest

from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.postgres._database import PostgresPool, fetchone


@dataclass(frozen=True, slots=True)
class DockerTestService:
    container_id: str

    @classmethod
    def from_environment(cls, name: str) -> DockerTestService:
        container_id = os.environ.get(name)
        if container_id is None:
            if os.environ.get("AGENTOS_RUN_INTEGRATION") == "1":
                pytest.fail(f"{name} is required by the live backend suite")
            pytest.skip(f"set {name} to run backend restart tests")
        if not container_id or container_id.strip() != container_id:
            raise ValueError(f"{name} must be a non-empty Docker identifier")
        if any(character.isspace() for character in container_id):
            raise ValueError(f"{name} must not contain whitespace")
        return cls(container_id)

    def kill(self) -> None:
        self._run("kill", "--signal", "KILL", self.container_id)

    def start(self) -> None:
        self._run("start", self.container_id)

    def pause(self) -> None:
        self._run("pause", self.container_id)

    def unpause(self) -> None:
        self._run("unpause", self.container_id)

    @staticmethod
    def _run(*arguments: str) -> None:
        try:
            subprocess.run(
                ("docker", *arguments),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AssertionError(
                f"Docker test service command failed: {arguments[0]}"
            ) from error


async def wait_for_redis(redis_url: str) -> None:
    redis_asyncio = import_module("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url)

    async def probe() -> bool:
        try:
            return bool(await client.ping())
        except Exception:
            return False

    try:
        await _wait_for_probe(probe)
    finally:
        await client.aclose()


async def wait_for_postgres(pool: PostgresPool) -> None:
    async def probe() -> bool:
        try:
            async with pool.connection() as connection:
                row = await fetchone(connection, "SELECT 1 AS available")
            return row == {"available": 1}
        except DistributedBackendUnavailableError:
            return False

    await _wait_for_probe(probe)


async def cleanup_redis(redis_url: str, key_prefix: str) -> None:
    redis_asyncio = import_module("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url)
    try:
        keys = [key async for key in client.scan_iter(match=f"{key_prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


async def prioritize_outbox(pool: PostgresPool, outbox_id: str) -> None:
    async with pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_outbox AS target
            SET created_at = COALESCE(
                (
                    SELECT MIN(candidate.created_at) - interval '1 second'
                    FROM agentos_distributed_outbox AS candidate
                    WHERE candidate.outbox_id <> target.outbox_id
                ),
                target.created_at
            )
            WHERE target.outbox_id = %s
            """,
            (outbox_id,),
        )


async def _wait_for_probe(probe: Callable[[], Awaitable[bool]]) -> None:
    async def poll() -> None:
        while not await probe():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(poll(), timeout=15)


__all__ = [
    "DockerTestService",
    "cleanup_redis",
    "prioritize_outbox",
    "wait_for_postgres",
    "wait_for_redis",
]
