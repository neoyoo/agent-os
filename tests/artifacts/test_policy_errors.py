import asyncio

import pytest

from agentos.artifacts.runtime import ArtifactPolicy, ArtifactRuntime
from agentos.artifacts.in_memory import InMemoryArtifactStore
from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactTooLargeError,
)


def test_artifact_runtime_uses_typed_policy_errors() -> None:
    async def scenario() -> None:
        runtime = ArtifactRuntime(
            session_id="session_1",
            store=InMemoryArtifactStore(),
            policy=ArtifactPolicy(
                allowed_media_types=frozenset({"image/png"}),
                max_size_bytes=3,
            ),
        )

        with pytest.raises(
            ArtifactMediaTypeUnsupportedError,
            match="^unsupported artifact media type$",
        ):
            await runtime.upload(
                data=b"abc",
                filename="drawing.jpg",
                media_type="image/jpeg",
            )
        with pytest.raises(
            ArtifactTooLargeError,
            match="^artifact exceeds maximum size$",
        ):
            await runtime.upload(
                data=b"abcd",
                filename="drawing.png",
                media_type="image/png",
            )

    asyncio.run(scenario())
