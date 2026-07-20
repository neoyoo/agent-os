from __future__ import annotations

from dataclasses import dataclass, field

from agentos.artifacts.types import (
    ArtifactNotFoundError,
    ArtifactPage,
    ArtifactRecord,
    ArtifactValidationError,
    validate_artifact_id,
)
from agentos.distributed.models import ArtifactContent, RequestScope
from agentos.distributed.protocols import DistributedArtifactPort


@dataclass(slots=True)
class ClaimArtifactStore:
    """Bind Artifact reads to one authoritative claim scope and Session."""

    scope: RequestScope
    session_id: str
    port: DistributedArtifactPort
    _content: dict[str, ArtifactContent] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    async def put(
        self,
        *,
        session_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord:
        del session_id, data, filename, media_type
        raise ArtifactValidationError("claim artifact store is read-only")

    async def get(self, session_id: str, artifact_id: str) -> ArtifactRecord:
        return (await self._read_content(session_id, artifact_id)).record

    async def read(self, session_id: str, artifact_id: str) -> bytes:
        return (await self._read_content(session_id, artifact_id)).data

    async def list(
        self,
        session_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ArtifactPage:
        self._require_session(session_id)
        page = await self.port.list(
            scope=self.scope,
            session_id=self.session_id,
            cursor=cursor,
            limit=limit,
        )
        if type(page) is not ArtifactPage or any(
            record.session_id != self.session_id for record in page.items
        ):
            raise RuntimeError("artifact port returned another session page")
        return page

    async def delete(self, session_id: str, artifact_id: str) -> None:
        del session_id, artifact_id
        raise ArtifactValidationError("claim artifact store is read-only")

    async def delete_session(self, session_id: str) -> None:
        del session_id
        raise ArtifactValidationError("claim artifact store is read-only")

    async def _read_content(
        self,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent:
        self._require_session(session_id)
        validate_artifact_id(artifact_id)
        cached = self._content.get(artifact_id)
        if cached is not None:
            return cached
        content = await self.port.read(
            scope=self.scope,
            session_id=self.session_id,
            artifact_id=artifact_id,
        )
        if type(content) is not ArtifactContent or (
            content.record.session_id != self.session_id
            or content.record.id != artifact_id
        ):
            raise RuntimeError("artifact port returned another artifact")
        self._content[artifact_id] = content
        return content

    def _require_session(self, session_id: str) -> None:
        if session_id != self.session_id:
            raise ArtifactNotFoundError()


__all__ = ["ClaimArtifactStore"]
