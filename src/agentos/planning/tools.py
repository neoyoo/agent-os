from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from agentos.capabilities import RegisteredTool, ToolRegistry
from agentos.planning.decomposition import (
    PlanDecomposition,
    PlanDecompositionGatePolicy,
    PlanStepSpec,
)
from agentos.planning.errors import PlannerToolAuthorizationError
from agentos.planning.models import (
    EVIDENCE_KINDS,
    PLAN_STATUSES,
    EvidenceKind,
    PlanState,
    PlanStatus,
)
from agentos.planning.runtime import PlannerRuntime
from agentos.planning.tool_schemas import planner_tool_definitions
from agentos.planning.tool_serialization import (
    claimed_scheduler_tick_report_to_dict,
    dispatch_report_to_dict,
    evidence_to_dict,
    json_result,
    plan_to_dict,
    scheduler_tick_report_to_dict,
    step_to_dict,
)


class PlannerToolAuthorizationPolicy(Protocol):
    """Planner Tool 的显式授权边界。"""

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        """调用不被允许时抛出 PlannerToolAuthorizationError。"""


class DefaultPlannerToolAuthorizationPolicy:
    """默认拒绝会触发调度或分发副作用的 Planner Tool。"""

    _scheduler_tools: frozenset[str] = frozenset(
        {
            "plan_claim_schedulable_plans",
            "plan_claimed_scheduler_tick",
            "plan_dispatch_ready_steps",
            "plan_scheduler_tick",
        },
    )

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        if tool_name in self._scheduler_tools:
            raise PlannerToolAuthorizationError(
                f"planner tool requires explicit authorization: {tool_name}",
            )


class AllowAllPlannerToolAuthorizationPolicy:
    """供本地开发显式选择的全允许策略。"""

    def authorize_planner_tool(
        self,
        *,
        tool_name: str,
        owner_agent_id: str,
        plan_id: str | None,
    ) -> None:
        return None


class PlannerTools:
    """把 PlannerRuntime 操作注册为外部工具。"""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        owner_agent_id: str,
        authorization_policy: PlannerToolAuthorizationPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.owner_agent_id = owner_agent_id
        self.authorization_policy = (
            authorization_policy or DefaultPlannerToolAuthorizationPolicy()
        )

    def register(self, registry: ToolRegistry) -> None:
        handlers = {
            "plan_create": self._plan_create,
            "plan_gate_decomposition_proposal": self._plan_gate_proposal,
            "plan_create_from_decomposition": self._plan_create_from_decomposition,
            "plan_add_step": self._plan_add_step,
            "plan_assign_step": self._plan_assign_step,
            "plan_ready_steps": self._plan_ready_steps,
            "plan_schedulable_plans": self._plan_schedulable_plans,
            "plan_claim_schedulable_plans": self._plan_claim_schedulable_plans,
            "plan_claimed_scheduler_tick": self._plan_claimed_scheduler_tick,
            "plan_dispatch_ready_steps": self._plan_dispatch_ready_steps,
            "plan_scheduler_tick": self._plan_scheduler_tick,
            "plan_fail_step": self._plan_fail_step,
            "plan_retryable_steps": self._plan_retryable_steps,
            "plan_retry_step": self._plan_retry_step,
            "plan_record_evidence": self._plan_record_evidence,
            "plan_complete_step": self._plan_complete_step,
            "plan_status": self._plan_status,
        }
        for name, description, parameters in planner_tool_definitions():
            registry.register(
                RegisteredTool(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=handlers[name],
                ),
            )

    async def _plan_create(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_create", None)
        return json_result(
            plan_to_dict(
                await self.runtime.create_plan(
                    objective=str(arguments["objective"]),
                    owner_agent_id=self.owner_agent_id,
                    plan_id=_optional_str(arguments.get("plan_id")),
                ),
            ),
        )

    async def _plan_create_from_decomposition(
        self,
        arguments: dict[str, object],
    ) -> str:
        self._authorize("plan_create_from_decomposition", None)
        plan = await self.runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective=str(arguments["objective"]),
                steps=_plan_step_specs(arguments["steps"]),
            ),
            owner_agent_id=self.owner_agent_id,
            plan_id=_optional_str(arguments.get("plan_id")),
        )
        return json_result(plan_to_dict(plan))

    def _plan_gate_proposal(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_gate_decomposition_proposal", None)
        proposal = arguments.get("proposal")
        if not isinstance(proposal, Mapping):
            raise ValueError("proposal must be an object")
        report = self.runtime.gate_decomposition_proposal(
            proposal,
            policy=_decomposition_gate_policy(arguments.get("policy")),
            metadata=_object_mapping(arguments.get("metadata")),
        )
        return json_result(report.as_dict())

    async def _plan_add_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_add_step", plan_id)
        updated = await self.runtime.add_step(
            plan_id,
            instruction=str(arguments["instruction"]),
            required_capabilities=_string_tuple(
                arguments.get("required_capabilities", ()),
            ),
            template_id=_optional_str(arguments.get("template_id")),
        )
        return json_result(plan_to_dict(updated))

    async def _plan_status(self, arguments: dict[str, object]) -> str:
        if arguments.get("plan_id") is not None:
            plan_id = str(arguments["plan_id"])
            self._authorize("plan_status", plan_id)
            return json_result(plan_to_dict(await self._require_owned_plan(plan_id)))
        self._authorize("plan_status", None)
        return json_result(
            {
                "plans": [
                    plan_to_dict(plan)
                    for plan in await self.runtime.list_plans(self.owner_agent_id)
                ],
            },
        )

    async def _plan_assign_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_assign_step", plan_id)
        updated = await self.runtime.assign_step(
            plan_id,
            str(arguments["step_id"]),
            template_id=str(arguments["template_id"]),
        )
        return json_result(plan_to_dict(updated))

    async def _plan_ready_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_ready_steps", plan_id)
        return json_result(
            {
                "plan_id": plan_id,
                "ready_steps": [
                    step_to_dict(step) for step in await self.runtime.ready_steps(plan_id)
                ],
            },
        )

    async def _plan_dispatch_ready_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_dispatch_ready_steps", plan_id)
        report = await self.runtime.dispatch_ready_steps(
            plan_id,
            default_template_id=_optional_str(arguments.get("default_template_id")),
            limit=_optional_int(arguments.get("limit")),
        )
        return json_result(dispatch_report_to_dict(report))

    async def _plan_schedulable_plans(self, arguments: dict[str, object]) -> str:
        self._authorize("plan_schedulable_plans", None)
        summaries = await self.runtime.schedulable_plans(
            owner_agent_id=self.owner_agent_id,
            statuses=_plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=_optional_int(arguments.get("limit")),
        )
        return json_result({"plans": [item.as_dict() for item in summaries]})

    async def _plan_claim_schedulable_plans(
        self,
        arguments: dict[str, object],
    ) -> str:
        self._authorize("plan_claim_schedulable_plans", None)
        claims = await self.runtime.claim_schedulable_plans(
            owner_agent_id=self.owner_agent_id,
            worker_id=str(arguments["worker_id"]),
            lease_seconds=float(arguments["lease_seconds"]),
            statuses=_plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=_optional_int(arguments.get("limit")),
        )
        return json_result({"claims": [claim.as_dict() for claim in claims]})

    async def _plan_claimed_scheduler_tick(
        self,
        arguments: dict[str, object],
    ) -> str:
        self._authorize("plan_claimed_scheduler_tick", None)
        report = await self.runtime.claimed_scheduler_tick(
            owner_agent_id=self.owner_agent_id,
            worker_id=str(arguments["worker_id"]),
            lease_seconds=float(arguments["lease_seconds"]),
            statuses=_plan_status_tuple(
                arguments.get("statuses", ("draft", "running")),
            ),
            limit=_optional_int(arguments.get("limit")),
            default_template_id=_optional_str(arguments.get("default_template_id")),
            retry_limit=_optional_int(arguments.get("retry_limit")),
            dispatch_limit=_optional_int(arguments.get("dispatch_limit")),
            release_after_tick=bool(arguments.get("release_after_tick", False)),
        )
        return json_result(claimed_scheduler_tick_report_to_dict(report))

    async def _plan_scheduler_tick(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_scheduler_tick", plan_id)
        report = await self.runtime.scheduler_tick(
            plan_id,
            default_template_id=_optional_str(arguments.get("default_template_id")),
            retry_limit=_optional_int(arguments.get("retry_limit")),
            dispatch_limit=_optional_int(arguments.get("dispatch_limit")),
        )
        return json_result(scheduler_tick_report_to_dict(report))

    async def _plan_fail_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_fail_step", plan_id)
        updated = await self.runtime.fail_step(
            plan_id,
            str(arguments["step_id"]),
            error=str(arguments["error"]),
        )
        return json_result(plan_to_dict(updated))

    async def _plan_retryable_steps(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_retryable_steps", plan_id)
        return json_result(
            {
                "plan_id": plan_id,
                "retryable_steps": [
                    step_to_dict(step)
                    for step in await self.runtime.retryable_steps(plan_id)
                ],
            },
        )

    async def _plan_retry_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_retry_step", plan_id)
        updated = await self.runtime.retry_step(plan_id, str(arguments["step_id"]))
        return json_result(plan_to_dict(updated))

    async def _plan_record_evidence(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_record_evidence", plan_id)
        evidence = await self.runtime.record_evidence(
            plan_id,
            step_ids=_string_tuple(arguments.get("step_ids", ())),
            kind=_evidence_kind(arguments["kind"]),
            summary=str(arguments["summary"]),
            uri=_optional_str(arguments.get("uri")),
            producer_agent_id=_optional_str(arguments.get("producer_agent_id")),
            metadata=_string_mapping(arguments.get("metadata")),
        )
        return json_result(evidence_to_dict(evidence))

    async def _plan_complete_step(self, arguments: dict[str, object]) -> str:
        plan_id = str(arguments["plan_id"])
        await self._authorize_owned("plan_complete_step", plan_id)
        updated = await self.runtime.complete_step(
            plan_id,
            str(arguments["step_id"]),
            evidence_ids=_string_tuple(arguments.get("evidence_ids", ())),
        )
        return json_result(plan_to_dict(updated))

    def _authorize(self, tool_name: str, plan_id: str | None) -> None:
        self.authorization_policy.authorize_planner_tool(
            tool_name=tool_name,
            owner_agent_id=self.owner_agent_id,
            plan_id=plan_id,
        )

    async def _authorize_owned(self, tool_name: str, plan_id: str) -> None:
        self._authorize(tool_name, plan_id)
        await self._require_owned_plan(plan_id)

    async def _require_owned_plan(self, plan_id: str) -> PlanState:
        return await self.runtime.get_plan(
            plan_id,
            owner_agent_id=self.owner_agent_id,
        )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError("expected list of strings")
    return tuple(str(item) for item in value)


def _string_mapping(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("expected object with string values")
    return {str(key): str(item) for key, item in value.items()}


def _object_mapping(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return {str(key): item for key, item in value.items()}


def _decomposition_gate_policy(value: object) -> PlanDecompositionGatePolicy:
    if value is None:
        return PlanDecompositionGatePolicy()
    if not isinstance(value, dict):
        raise ValueError("policy must be an object")
    max_steps = value.get("max_steps")
    return PlanDecompositionGatePolicy(
        max_steps=None if max_steps is None else int(max_steps),
        require_template=bool(value.get("require_template", False)),
        require_approval=bool(value.get("require_approval", False)),
        approved=bool(value.get("approved", False)),
        allowed_template_ids=_string_tuple(value.get("allowed_template_ids", ())),
    )


def _evidence_kind(value: object) -> EvidenceKind:
    kind = str(value)
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unsupported evidence kind: {kind}")
    return cast(EvidenceKind, kind)


def _plan_status_tuple(value: object) -> tuple[PlanStatus, ...]:
    statuses = _string_tuple(value)
    if not statuses:
        raise ValueError("statuses must not be empty")
    for status in statuses:
        if status not in PLAN_STATUSES:
            raise ValueError(f"unsupported plan status: {status}")
    return cast(tuple[PlanStatus, ...], statuses)


def _plan_step_specs(value: object) -> tuple[PlanStepSpec, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("expected list of plan step specs")
    specs: list[PlanStepSpec] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("expected object plan step spec")
        specs.append(
            PlanStepSpec(
                instruction=str(item["instruction"]),
                step_id=_optional_str(item.get("step_id")),
                required_capabilities=_string_tuple(
                    item.get("required_capabilities", ()),
                ),
                template_id=_optional_str(item.get("template_id")),
                depends_on=_string_tuple(item.get("depends_on", ())),
            ),
        )
    return tuple(specs)
