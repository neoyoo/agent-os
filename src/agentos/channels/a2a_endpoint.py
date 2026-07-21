from __future__ import annotations

from dataclasses import dataclass
import math

from agentos.channels._a2a_errors import (
    A2AChannelOperationError,
    map_a2a_operation_error,
)
from agentos.channels._a2a_operations import A2AOperations
from agentos.channels.a2a_stream import A2ASseResponse
from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.distributed.models import RequestScope
from agentos.transports.a2a import MAX_A2A_JSON_BYTES
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.operation_types import (
    A2ACreateTaskPushNotificationConfigParams,
    A2AOperationError,
    A2AOperationRequest,
    A2AOperationResponse,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
)
from agentos.transports.a2a.protocol import (
    A2A_JSON_RPC_MEDIA_TYPE,
    A2A_SNAPSHOT_RESUME_EXTENSION,
    A2AExtensionNegotiationError,
    A2AExtensionPolicy,
    A2AProtocolVersionPolicy,
    parse_extensions_header,
)
from agentos.transports.a2a.serialization import (
    A2AWireDecodeError,
    decode_operation_request,
    encode_operation_response,
)
from agentos.transports.http.errors import (
    HttpTransportError,
    RequestTooLargeError,
    UnsupportedMediaTypeError,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.request_decoder import validate_content_length
from agentos.transports.http.response_encoder import encode_error_response
from agentos.transports.http.response_types import HttpResponse


@dataclass(frozen=True, slots=True)
class A2AEndpoint:
    """A2A JSON-RPC Channel：鉴权、协商并分派到 Application Service。"""

    services: ChannelServices
    authenticator: ChannelAuthenticator
    heartbeat_interval: float = 15.0
    interface_tenant: str | None = None

    def __post_init__(self) -> None:
        if type(self.services) is not ChannelServices:
            raise TypeError("services must be ChannelServices")
        if (
            type(self.heartbeat_interval) not in (int, float)
            or not math.isfinite(self.heartbeat_interval)
            or self.heartbeat_interval <= 0
        ):
            raise ValueError("heartbeat_interval must be positive")
        if self.interface_tenant is not None and (
            type(self.interface_tenant) is not str
            or not self.interface_tenant
            or self.interface_tenant != self.interface_tenant.strip()
        ):
            raise ValueError("interface_tenant must be a non-empty identifier or None")

    async def handle(
        self,
        *,
        headers: HttpHeaders,
        body: bytes,
        request_id: str,
    ) -> HttpResponse | A2ASseResponse:
        if type(headers) is not HttpHeaders:
            raise TypeError("headers must be HttpHeaders")
        if type(body) is not bytes:
            raise TypeError("body must be bytes")
        http_error = _http_gate(headers, body)
        if http_error is not None:
            return encode_error_response(http_error, request_id=request_id)
        try:
            request = decode_operation_request(body)
        except A2AWireDecodeError as error:
            return _operation_error_response(error.request_id, error.error)
        try:
            scope = await self.authenticator.authenticate(
                headers,
                context=_auth_context(request),
            )
        except Exception as error:
            return encode_error_response(error, request_id=request_id)
        try:
            A2AProtocolVersionPolicy().negotiate(_header(headers, "a2a-version"))
            requested_extensions = _requested_extensions(headers)
            public_card = await _public_card(self.services, scope)
            accepted_extensions = _negotiate_extensions(
                public_card,
                requested_extensions,
            )
            last_event_id = _header(headers, "last-event-id")
            if last_event_id is not None:
                if A2A_SNAPSHOT_RESUME_EXTENSION not in accepted_extensions:
                    raise A2AChannelOperationError(-32008)
            _validate_interface(public_card, self.interface_tenant)
            _validate_tenant_hint(request, self.interface_tenant)
            if last_event_id is not None:
                if request.method != "SubscribeToTask":
                    raise A2AChannelOperationError(-32602)
            result = await A2AOperations(
                services=self.services,
                scope=scope,
                public_card=public_card,
                opaque_request_id=request_id,
                heartbeat_interval=float(self.heartbeat_interval),
            ).dispatch(request, last_event_id=last_event_id)
            if type(result) is A2ASseResponse:
                return result
            return _operation_success_response(request, result)
        except Exception as error:
            mapped = map_a2a_operation_error(error, method=request.method)
            return _operation_error_response(request.request_id, mapped)


def _http_gate(headers: HttpHeaders, body: bytes) -> HttpTransportError | None:
    if _header(headers, "content-type") != A2A_JSON_RPC_MEDIA_TYPE:
        return UnsupportedMediaTypeError()
    if len(body) > MAX_A2A_JSON_BYTES:
        return RequestTooLargeError()
    try:
        validate_content_length(headers, len(body))
    except HttpTransportError as error:
        return error
    return None


def _auth_context(request: A2AOperationRequest) -> ChannelAuthContext:
    params = request.params
    session_id = None
    resource_id = None
    resource_type = "a2a"
    if isinstance(params, (A2ASendMessageParams, A2ASendStreamingMessageParams)):
        session_id = params.message.context_id
        resource_id = params.message.task_id or params.message.message_id
        resource_type = "task" if params.message.task_id else "message"
    elif type(params) is A2ACreateTaskPushNotificationConfigParams:
        resource_id = params.config.task_id
        resource_type = "task"
    else:
        session_id = getattr(params, "context_id", None)
        resource_id = getattr(params, "task_id", None) or getattr(params, "id", None)
        resource_type = (
            "task"
            if resource_id is not None
            else "task_catalog"
            if session_id is not None
            else "a2a"
        )
    return ChannelAuthContext(
        operation=request.method,
        method="POST",
        path="/a2a",
        session_id=session_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _validate_tenant_hint(
    request: A2AOperationRequest,
    interface_tenant: str | None,
) -> None:
    params = request.params
    hints = [getattr(params, "tenant", None)]
    if type(params) is A2ACreateTaskPushNotificationConfigParams:
        hints.append(params.config.tenant)
    if isinstance(params, (A2ASendMessageParams, A2ASendStreamingMessageParams)):
        configuration = params.configuration
        if configuration is not None and configuration.task_push_notification_config:
            hints.append(configuration.task_push_notification_config.tenant)
    if any(hint is not None and hint != interface_tenant for hint in hints):
        raise A2AChannelOperationError(-32602)


def _validate_interface(
    card: A2AAgentCard,
    interface_tenant: str | None,
) -> None:
    if not any(
        interface.protocol_binding == "JSONRPC"
        and interface.protocol_version == "1.0"
        and interface.tenant == interface_tenant
        for interface in card.supported_interfaces
    ):
        raise A2AChannelOperationError(-32006)


async def _public_card(
    services: ChannelServices,
    scope: RequestScope,
) -> A2AAgentCard:
    provider = services.a2a_cards
    if provider is None:
        raise A2AChannelOperationError(-32004)
    card = await provider.get_public_card(scope=scope)
    if type(card) is not A2AAgentCard:
        raise A2AChannelOperationError(-32006)
    return card


def _requested_extensions(headers: HttpHeaders) -> tuple[str, ...]:
    try:
        return parse_extensions_header(_header(headers, "a2a-extensions"))
    except ValueError:
        raise A2AChannelOperationError(-32602) from None


def _negotiate_extensions(
    card: A2AAgentCard,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    extensions = card.capabilities.extensions
    if not any(
        extension.uri == A2A_SNAPSHOT_RESUME_EXTENSION
        and extension.required is False
        for extension in extensions
    ):
        raise A2AChannelOperationError(-32006)
    try:
        result = A2AExtensionPolicy(
            supported=tuple(extension.uri for extension in extensions),
            required=tuple(
                extension.uri for extension in extensions if extension.required
            ),
        ).negotiate(requested=requested)
    except A2AExtensionNegotiationError:
        raise
    except ValueError:
        raise A2AChannelOperationError(-32006) from None
    return result.accepted


def _header(headers: HttpHeaders, name: str) -> str | None:
    values = headers.get_all(name)
    return None if not values else values[0]


def _operation_success_response(
    request: A2AOperationRequest,
    result: object,
) -> HttpResponse:
    try:
        response = A2AOperationResponse(request.request_id, result=result)
        return _json_rpc_response(encode_operation_response(response))
    except (TypeError, ValueError):
        return _operation_error_response(
            request.request_id,
            A2AOperationError(-32006),
        )


def _operation_error_response(
    request_id: str | int | None,
    error: A2AOperationError,
) -> HttpResponse:
    response = A2AOperationResponse(request_id, error=error)
    return _json_rpc_response(encode_operation_response(response))


def _json_rpc_response(body: bytes) -> HttpResponse:
    return HttpResponse(
        200,
        (
            ("Content-Type", A2A_JSON_RPC_MEDIA_TYPE),
            ("Content-Length", str(len(body))),
        ),
        body,
    )


__all__ = ["A2AEndpoint"]
