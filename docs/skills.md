# Skills

Skills are loaded through an async `SkillContentSource`. `SkillRegistry.aload(...)`
reads source metadata once during startup, while `load_skill` and
`load_skill_resource` can await backing stores such as Redis, HTTP, or a file
system thread boundary.

## Redis-backed SkillContentSource (webagent example)

Redis key naming:

- `skills:index` -> set of skill names
- `skill:<name>` -> skill metadata and content hash
- `skill:<name>:resources` -> set of resource paths
- `skill:<name>:resource:<path>` -> individual resource metadata and content

```python
import json

import redis.asyncio as redis

from agentos.capabilities import (
    SkillContentSource,
    SkillDefinition,
    SkillLoadResult,
    SkillResourceLoadResult,
    SkillResourceRef,
)


class RedisSkillSource(SkillContentSource):
    def __init__(self, pool: redis.ConnectionPool):
        self._redis = redis.Redis(connection_pool=pool)

    async def list_skills(self) -> list[SkillDefinition]:
        names = await self._redis.smembers("skills:index")
        async with self._redis.pipeline(transaction=False) as pipe:
            for name in names:
                pipe.hgetall(f"skill:{name.decode()}")
            raw_list = await pipe.execute()
        return [self._decode_skill(raw) for raw in raw_list]

    async def load_skill(self, name: str) -> SkillLoadResult:
        raw = await self._redis.hgetall(f"skill:{name}")
        if not raw:
            raise KeyError(name)
        return SkillLoadResult(
            name=name,
            content=raw[b"content"].decode(),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        index = await self._redis.smembers(f"skill:{name}:resources")
        if not index:
            return ()
        async with self._redis.pipeline(transaction=False) as pipe:
            for path in index:
                pipe.hgetall(f"skill:{name}:resource:{path.decode()}")
            raw_list = await pipe.execute()
        return tuple(
            self._decode_resource(path.decode(), raw)
            for path, raw in zip(index, raw_list)
        )

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        raw = await self._redis.hgetall(f"skill:{name}:resource:{path}")
        if not raw:
            raise KeyError(path)
        return SkillResourceLoadResult(
            skill_name=name,
            path=path,
            content=raw[b"content"].decode(),
            mime_type=raw.get(b"mime_type", b"text/plain").decode(),
        )

    async def load_resources(
        self,
        name: str,
        paths,
    ) -> list[SkillResourceLoadResult]:
        async with self._redis.pipeline(transaction=False) as pipe:
            for path in paths:
                pipe.hgetall(f"skill:{name}:resource:{path}")
            raw_list = await pipe.execute()
        return [
            SkillResourceLoadResult(
                skill_name=name,
                path=path,
                content=raw[b"content"].decode(),
                mime_type=raw.get(b"mime_type", b"text/plain").decode(),
            )
            for path, raw in zip(paths, raw_list)
            if raw
        ]

    def _decode_skill(self, raw: dict[bytes, bytes]) -> SkillDefinition:
        payload = json.loads(raw[b"metadata"].decode())
        return SkillDefinition(
            name=payload["name"],
            description=payload.get("description", ""),
            when_to_use=payload.get("when_to_use", payload.get("description", "")),
            content=raw[b"content"].decode(),
        )

    def _decode_resource(
        self,
        path: str,
        raw: dict[bytes, bytes],
    ) -> SkillResourceRef:
        return SkillResourceRef(
            path=path,
            mime_type=raw.get(b"mime_type", b"text/plain").decode(),
        )
```
