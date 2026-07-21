from __future__ import annotations

from dataclasses import dataclass

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.transports.http.request_decoder import (
    decode_artifact_deletion_id,
    decode_artifact_list_request,
    decode_artifact_upload,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_encoder import (
    encode_artifact_content_response,
    encode_artifact_delete_response,
    encode_artifact_page_response,
    encode_artifact_response,
    encode_error_response,
)
from agentos.transports.http.response_types import HttpResponse


@dataclass(frozen=True, slots=True)
class ArtifactEndpoint:
    """ASGI-agnostic Artifact Channel over ArtifactService."""

    services: ChannelServices
    authenticator: ChannelAuthenticator

    async def upload(
        self,
        *,
        session_id: str,
        headers: HttpHeaders,
        body: bytes,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context("upload_artifact", "POST", session_id),
            )
            request = decode_artifact_upload(
                session_id=session_id,
                headers=headers,
                body=body,
            )
            record = await self.services.artifacts.upload(
                scope,
                request.session_id,
                request.upload_id,
                request.data,
                request.filename,
                request.media_type,
            )
            return encode_artifact_response(record)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)

    async def list(
        self,
        *,
        session_id: str,
        headers: HttpHeaders,
        cursor: str | None,
        limit: str | None,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context("list_artifacts", "GET", session_id),
            )
            request = decode_artifact_list_request(cursor=cursor, limit=limit)
            page = await self.services.artifacts.list(
                scope,
                session_id,
                request.cursor,
                request.limit,
            )
            return encode_artifact_page_response(page)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)

    async def read(
        self,
        *,
        session_id: str,
        artifact_id: str,
        headers: HttpHeaders,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context(
                    "read_artifact",
                    "GET",
                    session_id,
                    artifact_id=artifact_id,
                ),
            )
            content = await self.services.artifacts.read(
                scope,
                session_id,
                artifact_id,
            )
            return encode_artifact_content_response(content)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)

    async def delete(
        self,
        *,
        session_id: str,
        artifact_id: str,
        headers: HttpHeaders,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context(
                    "delete_artifact",
                    "DELETE",
                    session_id,
                    artifact_id=artifact_id,
                ),
            )
            deletion_id = decode_artifact_deletion_id(headers)
            await self.services.artifacts.delete(
                scope,
                session_id,
                artifact_id,
                deletion_id,
            )
            return encode_artifact_delete_response()
        except Exception as error:
            return encode_error_response(error, request_id=request_id)


def _context(
    operation: str,
    method: str,
    session_id: str,
    *,
    artifact_id: str | None = None,
) -> ChannelAuthContext:
    path = f"/v1/sessions/{session_id}/artifacts"
    if artifact_id is not None:
        path += f"/{artifact_id}"
    return ChannelAuthContext(
        operation=operation,
        method=method,
        path=path,
        session_id=session_id,
        resource_type="artifact" if artifact_id is not None else "session",
        resource_id=artifact_id if artifact_id is not None else session_id,
    )


__all__ = ["ArtifactEndpoint"]
