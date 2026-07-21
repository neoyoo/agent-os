from __future__ import annotations

from dataclasses import dataclass

from agentos.channels._a2a_errors import A2AChannelOperationError
from agentos.channels._a2a_projection import push_input, push_view
from agentos.channels.service_wiring import ChannelServices
from agentos.distributed.a2a_services import A2APushService
from agentos.distributed.models import RequestScope
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.identity import a2a_operation_identity
from agentos.transports.a2a.operation_types import (
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AEmptyResult,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTaskPushNotificationConfigsResult,
    A2AOperationRequest,
)
from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig


@dataclass(frozen=True, slots=True)
class A2APushOperations:
    services: ChannelServices
    scope: RequestScope
    public_card: A2AAgentCard

    async def create(
        self,
        request: A2AOperationRequest,
        params: A2ACreateTaskPushNotificationConfigParams,
    ) -> A2ATaskPushNotificationConfig:
        pushes = self._pushes()
        config = params.config
        if config.task_id is None:
            raise A2AChannelOperationError(-32602)
        tasks = self.services.a2a_tasks
        if tasks is None:
            raise A2AChannelOperationError(-32004)
        binding = await tasks.resolve(self.scope, config.task_id)
        view = await pushes.create(
            self.scope,
            binding=binding,
            config_input=push_input(config),
            operation_id=a2a_operation_identity(
                method="CreateTaskPushNotificationConfig",
                request_id=request.request_id,
                task_id=binding.task_id,
                config_id=config.id,
            ),
        )
        return push_view(view)

    async def get(
        self,
        params: A2AGetTaskPushNotificationConfigParams,
    ) -> A2ATaskPushNotificationConfig:
        view = await self._pushes().get(
            self.scope,
            task_id=params.task_id,
            config_id=params.id,
        )
        return push_view(view)

    async def list(
        self,
        params: A2AListTaskPushNotificationConfigsParams,
    ) -> A2AListTaskPushNotificationConfigsResult:
        page = await self._pushes().list(
            self.scope,
            task_id=params.task_id,
            page_size=50 if params.page_size is None else params.page_size,
            page_token=params.page_token,
        )
        return A2AListTaskPushNotificationConfigsResult(
            configs=tuple(push_view(config) for config in page.configs),
            next_page_token=page.next_page_token,
        )

    async def delete(
        self,
        request: A2AOperationRequest,
        params: A2ADeleteTaskPushNotificationConfigParams,
    ) -> A2AEmptyResult:
        await self._pushes().delete(
            self.scope,
            task_id=params.task_id,
            config_id=params.id,
            operation_id=a2a_operation_identity(
                method="DeleteTaskPushNotificationConfig",
                request_id=request.request_id,
                task_id=params.task_id,
                config_id=params.id,
            ),
        )
        return A2AEmptyResult()

    def _pushes(self) -> A2APushService:
        if self.public_card.capabilities.push_notifications is not True:
            raise A2AChannelOperationError(-32003)
        pushes = self.services.a2a_push
        if pushes is None:
            raise A2AChannelOperationError(-32003)
        return pushes


__all__ = ["A2APushOperations"]
