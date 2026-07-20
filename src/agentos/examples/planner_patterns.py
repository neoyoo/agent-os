from __future__ import annotations

import argparse
import asyncio
import json
from typing import Sequence

from agentos.context import ContextSnapshotRenderer
from agentos.multi import (
    AgentCoordinatorPlanStepDispatcher,
    TaskHandle,
)
from agentos.planning import (
    AuthorizedPlanSource,
    BoundPlanProjectionProvider,
    InMemoryPlanStore,
    PlannerRuntime,
    SubAgentTemplate,
)
from agentos.tokens import HeuristicTokenCounter


class StaticPlannerCoordinator:
    """Small coordinator stub for deterministic planner examples."""

    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []

    def spawn(self, **kwargs: object) -> TaskHandle:
        self.spawn_calls.append(kwargs)
        return TaskHandle(
            task_id="task_spawn",
            mode="spawn",
            target_agent_id="spawned_worker",
            status="queued",
        )

    def dispatch(self, **kwargs: object) -> TaskHandle:
        self.dispatch_calls.append(kwargs)
        return TaskHandle(
            task_id="task_dispatch",
            mode="dispatch",
            target_agent_id="architecture_expert",
            status="queued",
        )


async def build_intent_router_example(
    *,
    query: str,
) -> str:
    """Route one request into a template-bound plan step."""

    store = InMemoryPlanStore()
    runtime = PlannerRuntime(
        store=store,
        templates=(
            SubAgentTemplate(
                template_id="architecture-reviewer",
                name="Architecture Reviewer",
                role="Review SDK boundaries.",
                capabilities=("architecture-review",),
                target_agent_id="architecture_expert",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=_sequential_id_factory(),
    )
    plan = await runtime.create_plan(
        objective=f"Route request: {query}",
        owner_agent_id="router",
        plan_id="intent_plan",
    )
    routed_intent = _classify_intent(query)
    plan = await runtime.add_step(
        plan.plan_id,
        instruction=f"Handle request as {routed_intent}.",
        required_capabilities=(routed_intent,),
        template_id="architecture-reviewer",
    )
    return await _render_active_plan(
        store=store,
        plan_id=plan.plan_id,
        owner_agent_id=plan.owner_agent_id,
    )


async def build_plan_and_execute_example() -> str:
    """Create a two-step plan, assign one step, and project the current state."""

    coordinator = StaticPlannerCoordinator()
    store = InMemoryPlanStore()
    runtime = PlannerRuntime(
        store=store,
        dispatcher=AgentCoordinatorPlanStepDispatcher(coordinator),  # type: ignore[arg-type]
        templates=(
            SubAgentTemplate(
                template_id="architecture-reviewer",
                name="Architecture Reviewer",
                role="Review SDK boundaries.",
                capabilities=("architecture-review",),
                target_agent_id="architecture_expert",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=_sequential_id_factory(),
    )
    plan = await runtime.create_plan(
        objective="Review planner pattern support.",
        owner_agent_id="planner",
        plan_id="execute_plan",
    )
    plan = await runtime.add_step(
        plan.plan_id,
        instruction="Review the planner runtime boundary.",
        required_capabilities=("architecture-review",),
        template_id="architecture-reviewer",
    )
    await runtime.add_step(
        plan.plan_id,
        instruction="Summarize the remaining production gaps.",
        required_capabilities=("docs",),
    )
    await runtime.assign_step(
        plan.plan_id,
        "step_1",
        template_id="architecture-reviewer",
    )
    evidence = await runtime.record_evidence(
        plan.plan_id,
        step_ids=("step_1",),
        kind="task_result",
        summary="Architecture reviewer confirmed the boundary.",
        producer_agent_id="architecture_expert",
    )
    plan = await runtime.complete_step(
        plan.plan_id,
        "step_1",
        evidence_ids=(evidence.evidence_id,),
    )
    return await _render_active_plan(
        store=store,
        plan_id=plan.plan_id,
        owner_agent_id=plan.owner_agent_id,
    )


async def main(argv: Sequence[str] | None = None) -> int:
    """Print deterministic planner context projections."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query",
        nargs="?",
        default="Please review the planner runtime boundary.",
    )
    args = parser.parse_args(argv)

    print(
        json.dumps(
            {
                "intent_router": await build_intent_router_example(query=args.query),
                "plan_and_execute": await build_plan_and_execute_example(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return 0


def _classify_intent(query: str) -> str:
    lowered = query.lower()
    if "review" in lowered or "boundary" in lowered:
        return "architecture-review"
    return "general"


def _sequential_id_factory():
    counts: dict[str, int] = {}

    def next_id(prefix: str) -> str:
        counts[prefix] = counts.get(prefix, 0) + 1
        return f"{prefix}_{counts[prefix]}"

    return next_id


async def _render_active_plan(
    *,
    store: InMemoryPlanStore,
    plan_id: str,
    owner_agent_id: str,
) -> str:
    provider = BoundPlanProjectionProvider(
        source=AuthorizedPlanSource(store),
        plan_id=plan_id,
        owner_agent_id=owner_agent_id,
    )
    await provider.prepare_projection_cache()
    return ContextSnapshotRenderer(HeuristicTokenCounter()).render(
        provider.projections(),
    ).xml


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
