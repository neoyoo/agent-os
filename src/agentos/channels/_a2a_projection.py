from __future__ import annotations

from agentos.channels._a2a_errors import A2AChannelOperationError
from agentos.distributed.a2a_models import (
    A2APushAuthenticationInput,
    A2APushConfigInput,
    A2APushConfigView,
    A2ATaskListQuery,
    A2ATaskState as DistributedA2ATaskState,
)
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.message_types import A2AMessage, A2ATaskState
from agentos.transports.a2a.operation_types import A2AListTasksParams
from agentos.transports.a2a.push_types import (
    A2AAuthenticationInfo,
    A2ATaskPushNotificationConfig,
)


def validate_message_modes(message: A2AMessage, card: A2AAgentCard) -> None:
    supported = frozenset(card.default_input_modes)
    for part in message.parts:
        if type(part.text) is str:
            mode = "text/plain"
        elif type(part.raw) is bytes:
            mode = part.media_type or "application/octet-stream"
        elif type(part.url) is str:
            raise A2AChannelOperationError(-32602)
        else:
            mode = "application/json"
        if mode not in supported:
            raise A2AChannelOperationError(-32005)


def validate_output_modes(
    accepted: tuple[str, ...],
    card: A2AAgentCard,
) -> None:
    if accepted and any(mode not in card.default_output_modes for mode in accepted):
        raise A2AChannelOperationError(-32005)


def list_query(params: A2AListTasksParams) -> A2ATaskListQuery:
    status = params.status
    normalized_status = None
    if status is not None:
        try:
            wire_status = A2ATaskState(status)
        except ValueError:
            raise A2AChannelOperationError(-32602) from None
        if wire_status is not A2ATaskState.TASK_STATE_UNSPECIFIED:
            normalized_status = DistributedA2ATaskState(wire_status.name)
    return A2ATaskListQuery(
        context_id=params.context_id,
        status=normalized_status,
        page_size=50 if params.page_size is None else params.page_size,
        page_token=params.page_token,
        history_length=params.history_length,
        status_timestamp_after=params.status_timestamp_after,
        include_artifacts=False
        if params.include_artifacts is None
        else params.include_artifacts,
    )


def push_input(config: A2ATaskPushNotificationConfig) -> A2APushConfigInput:
    authentication = config.authentication
    return A2APushConfigInput(
        config_id=config.id,
        url=config.url,
        token=config.token,
        authentication=(
            None
            if authentication is None
            else A2APushAuthenticationInput(
                authentication.scheme,
                authentication.credentials,
            )
        ),
    )


def push_view(view: A2APushConfigView) -> A2ATaskPushNotificationConfig:
    return A2ATaskPushNotificationConfig(
        task_id=view.task_id,
        id=view.config_id,
        url=view.url,
        authentication=(
            None
            if view.authentication_scheme is None
            else A2AAuthenticationInfo(view.authentication_scheme)
        ),
    )


__all__ = [
    "list_query",
    "push_input",
    "push_view",
    "validate_message_modes",
    "validate_output_modes",
]
