from __future__ import annotations

from dataclasses import dataclass

from agentos.channels._a2a_errors import A2AChannelOperationError
from agentos.channels._a2a_projection import list_query
from agentos.channels._a2a_push_operations import A2APushOperations
from agentos.channels._a2a_send import A2ASendOperations
from agentos.channels.a2a_stream import A2ASseResponse
from agentos.channels.service_wiring import ChannelServices
from agentos.distributed.a2a_services import A2ATaskService
from agentos.distributed.models import RequestScope
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.message_types import A2ATask
from agentos.transports.a2a.identity import a2a_operation_identity
from agentos.transports.a2a.mapping import (
    a2a_cancel_to_command,
    run_read_model_to_a2a_task,
)
from agentos.transports.a2a.operation_types import (
    A2ACancelTaskParams,
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AGetExtendedAgentCardParams,
    A2AGetTaskParams,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTasksParams,
    A2AListTasksResult,
    A2AOperationRequest,
    A2ASendMessageParams,
    A2ASendMessageResult,
    A2ASendStreamingMessageParams,
    A2AStreamResponse,
    A2ASubscribeToTaskParams,
)
from agentos.transports.run_stream import decode_cursor


_TERMINAL = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
)
_STREAM_CLOSED = _TERMINAL | {RunStatus.WAITING}


@dataclass(frozen=True, slots=True)
class A2AOperations:
    services: ChannelServices
    scope: RequestScope
    public_card: A2AAgentCard
    opaque_request_id: str
    heartbeat_interval: float

    async def dispatch(
        self,
        request: A2AOperationRequest,
        *,
        last_event_id: str | None,
    ) -> object | A2ASseResponse:
        params = request.params
        if type(params) is A2ASendMessageParams:
            configuration = params.configuration
            run = await self._send().execute(
                params,
                wait=not (
                    configuration is not None
                    and configuration.return_immediately is True
                ),
            )
            history_length = (
                None if configuration is None else configuration.history_length
            )
            return A2ASendMessageResult(
                task=run_read_model_to_a2a_task(
                    run,
                    history_length=history_length,
                ),
            )
        if type(params) is A2ASendStreamingMessageParams:
            return await self._send_streaming(request, params)
        if type(params) is A2AGetTaskParams:
            return await self._get_task(params)
        if type(params) is A2AListTasksParams:
            return await self._list_tasks(params)
        if type(params) is A2ACancelTaskParams:
            return await self._cancel(request, params)
        if type(params) is A2ASubscribeToTaskParams:
            return await self._subscribe(request, params, last_event_id)
        if type(params) is A2ACreateTaskPushNotificationConfigParams:
            return await self._push().create(request, params)
        if type(params) is A2AGetTaskPushNotificationConfigParams:
            return await self._push().get(params)
        if type(params) is A2AListTaskPushNotificationConfigsParams:
            return await self._push().list(params)
        if type(params) is A2ADeleteTaskPushNotificationConfigParams:
            return await self._push().delete(request, params)
        if type(params) is A2AGetExtendedAgentCardParams:
            return await self._extended_card()
        raise RuntimeError("decoded an unsupported A2A operation")

    async def _send_streaming(
        self,
        request: A2AOperationRequest,
        params: A2ASendStreamingMessageParams,
    ) -> A2ASseResponse:
        if self.public_card.capabilities.streaming is not True:
            raise A2AChannelOperationError(-32004)
        run = await self._send().execute(params, wait=False)
        barrier = await self.services.run_events.capture_high_water(
            self.scope,
            run.session_id,
            run.run_id,
        )
        snapshot = await self.services.run_queries.get(
            self.scope,
            run.session_id,
            run.run_id,
        )
        subscription = None
        if snapshot.status not in _STREAM_CLOSED:
            subscription = await self.services.run_events.follow_after(
                self.scope,
                snapshot.session_id,
                snapshot.run_id,
                barrier,
            )
        return A2ASseResponse(
            subscription,
            request_id=request.request_id,
            opaque_request_id=self.opaque_request_id,
            initial_response=A2AStreamResponse(
                task=run_read_model_to_a2a_task(
                    snapshot,
                    history_length=(
                        None
                        if params.configuration is None
                        else params.configuration.history_length
                    ),
                ),
            ),
            heartbeat_interval=self.heartbeat_interval,
            close_after_initial=snapshot.status is RunStatus.WAITING,
        )

    async def _get_task(self, params: A2AGetTaskParams) -> A2ATask:
        binding = await self._tasks().resolve(self.scope, params.id)
        run = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        return run_read_model_to_a2a_task(run, history_length=params.history_length)

    async def _list_tasks(self, params: A2AListTasksParams) -> A2AListTasksResult:
        catalog = self.services.a2a_catalog
        if catalog is None:
            raise A2AChannelOperationError(-32004)
        query = list_query(params)
        page = await catalog.list(self.scope, query)
        return A2AListTasksResult(
            tasks=tuple(
                run_read_model_to_a2a_task(
                    item.run,
                    history_length=query.history_length,
                    include_artifacts=query.include_artifacts,
                    status_timestamp=item.status_updated_at,
                )
                for item in page.items
            ),
            next_page_token=page.next_page_token,
            page_size=page.page_size,
            total_size=page.total_size,
        )

    async def _cancel(
        self,
        request: A2AOperationRequest,
        params: A2ACancelTaskParams,
    ) -> A2ATask:
        binding = await self._tasks().resolve(self.scope, params.id)
        run = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        if run.status is RunStatus.CANCELLED:
            return run_read_model_to_a2a_task(run)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise A2AChannelOperationError(-32002)
        operation_id = a2a_operation_identity(
            method="CancelTask",
            request_id=request.request_id,
            task_id=binding.task_id,
            config_id=None,
        )
        await self.services.run_commands.submit(
            self.scope,
            binding.session_id,
            a2a_cancel_to_command(
                params,
                run_id=binding.run_id,
                command_id=operation_id,
            ),
        )
        updated = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        return run_read_model_to_a2a_task(updated)

    async def _subscribe(
        self,
        request: A2AOperationRequest,
        params: A2ASubscribeToTaskParams,
        last_event_id: str | None,
    ) -> A2ASseResponse:
        if self.public_card.capabilities.streaming is not True:
            raise A2AChannelOperationError(-32004)
        binding = await self._tasks().resolve(self.scope, params.id)
        if last_event_id is not None:
            decode_cursor(
                last_event_id,
                tenant_id=self.scope.tenant_id,
                session_id=binding.session_id,
                run_id=binding.run_id,
            )
        barrier = await self.services.run_events.capture_high_water(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        run = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        if run.status in _STREAM_CLOSED:
            raise A2AChannelOperationError(-32004)
        subscription = await self.services.run_events.follow_after(
            self.scope,
            binding.session_id,
            binding.run_id,
            barrier,
        )
        return A2ASseResponse(
            subscription,
            request_id=request.request_id,
            opaque_request_id=self.opaque_request_id,
            initial_response=A2AStreamResponse(
                task=run_read_model_to_a2a_task(run),
            ),
            heartbeat_interval=self.heartbeat_interval,
        )

    async def _extended_card(self) -> A2AAgentCard:
        if self.public_card.capabilities.extended_agent_card is not True:
            raise A2AChannelOperationError(-32004)
        provider = self.services.a2a_cards
        if provider is None:
            raise A2AChannelOperationError(-32007)
        card = await provider.get_extended_card(scope=self.scope)
        if card is None:
            raise A2AChannelOperationError(-32007)
        if type(card) is not A2AAgentCard:
            raise A2AChannelOperationError(-32006)
        return card

    def _send(self) -> A2ASendOperations:
        return A2ASendOperations(self.services, self.scope, self.public_card)

    def _push(self) -> A2APushOperations:
        return A2APushOperations(self.services, self.scope, self.public_card)

    def _tasks(self) -> A2ATaskService:
        tasks = self.services.a2a_tasks
        if tasks is None:
            raise A2AChannelOperationError(-32004)
        return tasks

__all__ = ["A2AOperations"]
