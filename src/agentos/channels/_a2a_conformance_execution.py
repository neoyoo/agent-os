from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agentos.channels.a2a_operations import (
    A2AMessage,
    A2AOperationServer,
    A2ATask,
)


class _StaticA2AOperationRunner:
    """为 conformance harness 提供确定性的 A2A operation runner。"""

    async def send_message(self, message: A2AMessage) -> A2ATask:
        """把输入消息投影为已完成任务。"""

        return A2ATask(
            task_id=message.task_id or "task_1",
            context_id=message.context_id,
            state="completed",
            messages=(message,),
        )


def _run_a2a_operation(
    server: A2AOperationServer,
    operation_payload: Mapping[str, object],
    *,
    headers: Mapping[str, str] | None,
) -> dict[str, object]:
    """在同步 conformance harness 中执行异步 A2A operation server。"""

    return asyncio.run(server.handle_operation(operation_payload, headers=headers))
