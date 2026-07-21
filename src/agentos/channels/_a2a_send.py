from __future__ import annotations

from dataclasses import dataclass

from agentos.channels._a2a_errors import A2AChannelOperationError
from agentos.channels._a2a_projection import (
    push_input,
    validate_message_modes,
    validate_output_modes,
)
from agentos.channels.service_wiring import ChannelServices
from agentos.distributed.a2a_models import A2ATaskBinding
from agentos.distributed.a2a_services import A2ATaskService
from agentos.distributed.models import RequestScope, RunReadModel, StreamGap
from agentos.runtime.run_state import RunStatus
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a.identity import (
    a2a_artifact_upload_id,
    a2a_inline_push_identity,
    a2a_session_id,
)
from agentos.transports.a2a.mapping import (
    a2a_message_to_command,
    a2a_message_to_submission,
)
from agentos.transports.a2a.message_types import A2AMessage
from agentos.transports.a2a.operation_types import (
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
)
from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig


_STABLE_STATUSES = frozenset(
    {RunStatus.WAITING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
)


@dataclass(frozen=True, slots=True)
class A2ASendOperations:
    services: ChannelServices
    scope: RequestScope
    public_card: A2AAgentCard

    async def execute(
        self,
        params: A2ASendMessageParams | A2ASendStreamingMessageParams,
        *,
        wait: bool,
    ) -> RunReadModel:
        message = params.message
        configuration = params.configuration
        validate_message_modes(message, self.public_card)
        validate_output_modes(
            () if configuration is None else configuration.accepted_output_modes,
            self.public_card,
        )
        if message.task_id is None:
            binding, run = await self._submit_new(params)
        else:
            binding, run = await self._submit_follow_up(params)
        if configuration is not None and configuration.task_push_notification_config:
            await self._create_inline_push(
                binding,
                configuration.task_push_notification_config,
                message.message_id,
            )
        if wait and run.status not in _STABLE_STATUSES:
            run = await self._wait_until_stable(binding)
        return run

    async def _submit_new(
        self,
        params: A2ASendMessageParams | A2ASendStreamingMessageParams,
    ) -> tuple[A2ATaskBinding, RunReadModel]:
        message = params.message
        session_id = message.context_id or a2a_session_id(
            tenant_id=self.scope.tenant_id,
            message_id=message.message_id,
        )
        artifacts = await self._upload_raw_parts(message, session_id)
        receipt = await self.services.run_submissions.submit(
            self.scope,
            a2a_message_to_submission(
                message,
                session_id=session_id,
                artifact_handles=artifacts,
            ),
        )
        tasks = self._tasks()
        binding = await tasks.bind(
            self.scope,
            task_id=receipt.run_id,
            session_id=receipt.session_id,
            run_id=receipt.run_id,
        )
        run = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        return binding, run

    async def _submit_follow_up(
        self,
        params: A2ASendMessageParams | A2ASendStreamingMessageParams,
    ) -> tuple[A2ATaskBinding, RunReadModel]:
        message = params.message
        assert message.task_id is not None
        binding = await self._tasks().resolve(self.scope, message.task_id)
        if message.context_id is not None and message.context_id != binding.session_id:
            raise A2AChannelOperationError(-32602)
        run = await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )
        artifacts = await self._upload_raw_parts(message, binding.session_id)
        command = a2a_message_to_command(
            message,
            run=run,
            artifact_handles=artifacts,
        )
        await self.services.run_commands.submit(
            self.scope,
            binding.session_id,
            command,
        )
        return binding, await self.services.run_queries.get(
            self.scope,
            binding.session_id,
            binding.run_id,
        )

    async def _upload_raw_parts(
        self,
        message: A2AMessage,
        session_id: str,
    ) -> tuple[str, ...]:
        handles: list[str] = []
        for index, part in enumerate(message.parts):
            raw = part.raw
            if type(raw) is not bytes:
                continue
            record = await self.services.artifacts.upload(
                self.scope,
                session_id,
                a2a_artifact_upload_id(
                    message_id=message.message_id,
                    part_index=index,
                ),
                raw,
                part.filename,
                part.media_type or "application/octet-stream",
            )
            handles.append(record.id)
        return tuple(handles)

    async def _create_inline_push(
        self,
        binding: A2ATaskBinding,
        config: A2ATaskPushNotificationConfig,
        message_id: str,
    ) -> None:
        if self.public_card.capabilities.push_notifications is not True:
            raise A2AChannelOperationError(-32003)
        pushes = self.services.a2a_push
        if pushes is None:
            raise A2AChannelOperationError(-32003)
        if config.task_id is not None and config.task_id != binding.task_id:
            raise A2AChannelOperationError(-32602)
        await pushes.create(
            self.scope,
            binding=binding,
            config_input=push_input(config),
            operation_id=a2a_inline_push_identity(
                message_id=message_id,
                task_id=binding.task_id,
            ),
        )

    async def _wait_until_stable(self, binding: A2ATaskBinding) -> RunReadModel:
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
        if run.status in _STABLE_STATUSES:
            return run
        subscription = await self.services.run_events.follow_after(
            self.scope,
            binding.session_id,
            binding.run_id,
            barrier,
        )
        try:
            async for item in subscription:
                if type(item) is StreamGap:
                    raise RuntimeError("a2a wait stream gap")
                run = await self.services.run_queries.get(
                    self.scope,
                    binding.session_id,
                    binding.run_id,
                )
                if run.status in _STABLE_STATUSES:
                    return run
        finally:
            await subscription.aclose()
        raise RuntimeError("a2a wait stream ended before a stable state")

    def _tasks(self) -> A2ATaskService:
        tasks = self.services.a2a_tasks
        if tasks is None:
            raise A2AChannelOperationError(-32004)
        return tasks


__all__ = ["A2ASendOperations"]
