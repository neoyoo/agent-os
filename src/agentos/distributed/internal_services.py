from __future__ import annotations

from dataclasses import dataclass

from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.internal_protocols import InternalRunSubmissionPort
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope, RunSubmissionReceipt
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand


@dataclass(frozen=True, slots=True)
class InternalRunSubmissionService:
    """只供 Team Worker 使用的 internal-start Application Service。"""

    port: InternalRunSubmissionPort

    async def submit(
        self,
        scope: RequestScope,
        submission: InternalRunSubmission,
        authority: InternalSubmissionAuthority,
    ) -> RunSubmissionReceipt:
        """携带当前 claim authority 持久提交 internal Run。"""

        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        if type(submission) is not InternalRunSubmission:
            raise TypeError("submission must be InternalRunSubmission")
        if type(authority) is not InternalSubmissionAuthority:
            raise TypeError("authority must be InternalSubmissionAuthority")
        receipt = await self.port.submit_internal(
            scope=scope,
            submission=submission,
            authority=authority,
        )
        if type(receipt) is not RunSubmissionReceipt or (
            receipt.session_id != submission.session_id
            or receipt.submission_id != submission.submission_id
        ):
            raise RuntimeError("internal submission port returned another receipt")
        return receipt

    async def submit_wakeup(
        self,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
        authority: InternalSubmissionAuthority,
    ) -> DurableCommandReceipt:
        """携带当前 Team claim authority 提交可信 wakeup。"""

        _validate_scope_authority(scope, authority)
        require_identifier(session_id, "session_id")
        if type(command) is not DurableRunCommand or command.kind != "wakeup":
            raise TypeError("command must be a wakeup DurableRunCommand")
        receipt = await self.port.submit_wakeup(
            scope=scope,
            session_id=session_id,
            command=command,
            authority=authority,
        )
        if type(receipt) is not DurableCommandReceipt or (
            receipt.run_id != command.run_id
            or receipt.command_id != command.command_id
            or receipt.kind != "wakeup"
        ):
            raise RuntimeError("internal submission port returned another command receipt")
        return receipt

    async def get_applied_input(
        self,
        scope: RequestScope,
        authority: InternalSubmissionAuthority,
    ) -> InternalRunInputReceipt | None:
        """恢复同一 Team delivery 已接受的 Run 输入事实。"""

        _validate_scope_authority(scope, authority)
        receipt = await self.port.get_applied_input(
            scope=scope,
            authority=authority,
        )
        if receipt is not None and (
            type(receipt) is not InternalRunInputReceipt
            or receipt.delivery_id != authority.delivery_id
        ):
            raise RuntimeError("internal submission port returned another input receipt")
        return receipt


def _validate_scope_authority(
    scope: object,
    authority: object,
) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(authority) is not InternalSubmissionAuthority:
        raise TypeError("authority must be InternalSubmissionAuthority")


__all__ = ["InternalRunSubmissionService"]
