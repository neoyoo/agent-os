from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from agentos._waiting import WaitReason
from agentos.runtime import Agent, AgentResult


A2AExecutionState = Literal["completed", "working", "input-required"]
_MessageT = TypeVar("_MessageT")
_TaskT = TypeVar("_TaskT")


@dataclass(frozen=True, slots=True)
class A2AExecutionResult:
    """Agent outcome 到 A2A 协议投影之间的执行结果。"""

    state: A2AExecutionState
    content: str | None = None
    run_id: str | None = None
    wait_reason: WaitReason | None = None


async def execute_a2a_agent(agent: Agent, input: str) -> A2AExecutionResult:
    """通过统一 Agent 入口执行一次 A2A 输入。"""

    outcome = await agent.run(input)
    if isinstance(outcome, AgentResult):
        return A2AExecutionResult(state="completed", content=outcome.content)
    return A2AExecutionResult(
        state=(
            "input-required"
            if outcome.reason.kind == "human_input"
            else "working"
        ),
        run_id=outcome.run_id,
        wait_reason=outcome.reason,
    )


def a2a_wait_metadata(result: A2AExecutionResult) -> dict[str, object]:
    """把 WAITING 结果投影为 JSON-safe A2A metadata。"""

    reason = result.wait_reason
    if result.run_id is None or reason is None:
        return {}
    return {
        "runId": result.run_id,
        "waitReason": {
            "kind": reason.kind,
            "handle": reason.handle,
            "detail": reason.detail,
        },
    }


def project_a2a_task(
    result: A2AExecutionResult,
    *,
    task_id: str,
    context_id: str | None,
    request_message: _MessageT,
    make_text_part: Callable[[str], object],
    make_message: Callable[..., _MessageT],
    make_task: Callable[..., _TaskT],
) -> _TaskT:
    """把统一 Agent outcome 投影为调用方的 A2A task 类型。"""

    messages = (request_message,)
    if result.content is not None:
        messages += (
            make_message(
                role="agent",
                parts=(make_text_part(result.content),),
                context_id=context_id,
                task_id=task_id,
            ),
        )
    return make_task(
        task_id=task_id,
        context_id=context_id,
        state=result.state,
        messages=messages,
        metadata=a2a_wait_metadata(result),
    )


__all__ = [
    "A2AExecutionResult",
    "A2AExecutionState",
    "a2a_wait_metadata",
    "execute_a2a_agent",
    "project_a2a_task",
]
