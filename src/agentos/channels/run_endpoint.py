from __future__ import annotations

from dataclasses import dataclass

from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.transports.http.request_decoder import (
    decode_run_command,
    decode_run_submission,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_encoder import (
    encode_command_receipt_response,
    encode_error_response,
    encode_run_read_response,
    encode_submission_receipt_response,
)
from agentos.transports.http.response_types import HttpResponse


@dataclass(frozen=True, slots=True)
class RunEndpoint:
    """ASGI-agnostic Run Channel over Application Services."""

    services: ChannelServices
    authenticator: ChannelAuthenticator

    async def submit(
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
                context=_context("submit_run", "POST", session_id),
            )
            submission = decode_run_submission(
                session_id=session_id,
                headers=headers,
                body=body,
            )
            receipt = await self.services.run_submissions.submit(scope, submission)
            return encode_submission_receipt_response(receipt)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)

    async def command(
        self,
        *,
        session_id: str,
        run_id: str,
        headers: HttpHeaders,
        body: bytes,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context(
                    "submit_command",
                    "POST",
                    session_id,
                    run_id=run_id,
                ),
            )
            command = decode_run_command(run_id=run_id, headers=headers, body=body)
            receipt = await self.services.run_commands.submit(
                scope,
                session_id,
                command,
            )
            return encode_command_receipt_response(receipt)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)

    async def get(
        self,
        *,
        session_id: str,
        run_id: str,
        headers: HttpHeaders,
        request_id: str,
    ) -> HttpResponse:
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_context("query_run", "GET", session_id, run_id=run_id),
            )
            model = await self.services.run_queries.get(scope, session_id, run_id)
            return encode_run_read_response(model)
        except Exception as error:
            return encode_error_response(error, request_id=request_id)


def _context(
    operation: str,
    method: str,
    session_id: str,
    *,
    run_id: str | None = None,
) -> ChannelAuthContext:
    path = f"/v1/sessions/{session_id}/runs"
    if run_id is not None:
        path += f"/{run_id}"
        if operation == "submit_command":
            path += "/commands"
    return ChannelAuthContext(
        operation=operation,
        method=method,
        path=path,
        session_id=session_id,
        resource_type="run" if run_id is not None else "session",
        resource_id=run_id if run_id is not None else session_id,
    )


__all__ = ["RunEndpoint"]
