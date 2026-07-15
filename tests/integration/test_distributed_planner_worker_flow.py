from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentCoordinatorPlanStepDispatcher,
    ExpertAgentRunner,
    InMemoryPlanStore,
    InMemoryRegistry,
    PlanAssignment,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SpawnExecutor,
    SubagentInitRequest,
    SubAgentTemplate,
)
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.redis_queue import RedisAgentMessageQueue
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder
from tests._context_protocol_fixtures import default_context_renderer


pytestmark = pytest.mark.integration


def _require_live_backends() -> tuple[str, str]:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with docker-compose.test.yml services")
    postgres_dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN and AGENTOS_TEST_REDIS_URL")
    return postgres_dsn, redis_url


def _connect_postgres(dsn: str):
    try:
        import psycopg
    except ImportError as error:
        pytest.skip(f"install postgres extra to run live integration tests: {error}")
    return psycopg.connect(dsn)


def _connect_redis(url: str):
    try:
        import redis
    except ImportError as error:
        pytest.skip(f"install redis extra to run live integration tests: {error}")
    return redis.Redis.from_url(url)


def _migration_parts() -> tuple[str, str]:
    migration = Path(
        "docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql",
    ).read_text()
    up_part = migration.split("-- migrate:up", maxsplit=1)[1].split(
        "-- migrate:down",
        maxsplit=1,
    )[0]
    down_part = migration.split("-- migrate:down", maxsplit=1)[1]
    return up_part, down_part


def _reset_postgres_schema(dsn: str) -> None:
    up_part, down_part = _migration_parts()
    with _connect_postgres(dsn) as connection:
        connection.execute(down_part)
        connection.execute(up_part)
        connection.commit()


def _build_agent_with_response(content: str) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": FakeProvider([ProviderResponse(content=content)]),
        },
    )


class _StaticSubagentFactory:
    def create_subagent(self, request: SubagentInitRequest) -> Agent:
        return _build_agent_with_response("child result")


def _cleanup_redis_agent_streams(redis_client: object, key_prefix: str) -> None:
    redis_client.delete(  # type: ignore[attr-defined]
        f"{key_prefix}:multi:inbox:parent",
        f"{key_prefix}:multi:inbox:parent:dead",
        f"{key_prefix}:multi:inbox:parent:task_request",
        f"{key_prefix}:multi:inbox:parent:task_request:dead",
        f"{key_prefix}:multi:inbox:parent:task_result",
        f"{key_prefix}:multi:inbox:parent:task_result:dead",
        f"{key_prefix}:multi:inbox:parent:team_message",
        f"{key_prefix}:multi:inbox:parent:team_message:dead",
        f"{key_prefix}:multi:inbox:expert",
        f"{key_prefix}:multi:inbox:expert:dead",
        f"{key_prefix}:multi:inbox:expert:task_request",
        f"{key_prefix}:multi:inbox:expert:task_request:dead",
        f"{key_prefix}:multi:inbox:expert:task_result",
        f"{key_prefix}:multi:inbox:expert:task_result:dead",
        f"{key_prefix}:multi:inbox:expert:team_message",
        f"{key_prefix}:multi:inbox:expert:team_message:dead",
    )


def test_live_distributed_planner_dispatches_expert_worker_to_completion() -> None:
    postgres_dsn, redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    redis_client = _connect_redis(redis_url)
    key_prefix = f"agentos-test-{uuid4().hex}"
    task_store = PostgresTaskStore(
        dsn=postgres_dsn,
        connection=_connect_postgres(postgres_dsn),
    )
    queue = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="distributed-flow-consumer",
        allow_unscoped_consumers=True,
    )
    spawn_executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        message_queue=queue,
        task_store=task_store,
        spawn_executor=spawn_executor,
        subagent_factory=_StaticSubagentFactory(),
    )
    planner = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=AgentCoordinatorPlanStepDispatcher(coordinator),
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review one planner step.",
                capabilities=("review",),
                target_agent_id="expert",
            ),
        ),
        id_factory=lambda prefix: {
            "plan": "plan_live",
            "step": "step_live",
            "task": "task_live",
        }.get(str(prefix), f"{prefix}_live"),
    )

    try:
        coordinator.attach_agent(
            AgentCard(
                agent_id="parent",
                name="Parent",
                description="Planner owner.",
                capabilities=("coordinate",),
            ),
            _build_agent_with_response("parent unused"),
        )
        coordinator.attach_agent(
            AgentCard(
                agent_id="expert",
                name="Expert",
                description="Live state-plane expert.",
                capabilities=("review",),
            ),
            _build_agent_with_response("expert completed live work"),
        )
        plan = planner.create_plan(
            objective="Prove distributed planner worker state plane.",
            owner_agent_id="parent",
        )
        plan = planner.add_step(
            plan.plan_id,
            instruction="Review the live distributed harness.",
            required_capabilities=("review",),
            template_id="expert-reviewer",
        )

        dispatch_report = planner.dispatch_ready_steps(plan.plan_id)
        runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

        assert [assignment.step_id for assignment in dispatch_report.assigned] == [
            plan.steps[0].step_id,
        ]
        assert dispatch_report.skipped == ()
        assert runner.run_once(timeout=1.0) is True

        results = coordinator.collect_results("parent")
        stored_task = task_store.get(dispatch_report.assigned[0].task_id)
        assert len(results) == 1
        assert results[0].status == "completed"
        assert results[0].summary == "expert completed live work"
        assert stored_task is not None
        assert stored_task.status == "completed"
        assert stored_task.result == results[0]
    finally:
        _cleanup_redis_agent_streams(redis_client, key_prefix)
        task_store.close()
        spawn_executor.shutdown()


def test_live_distributed_planner_recovers_pending_dispatch_assignment() -> None:
    postgres_dsn, redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    redis_client = _connect_redis(redis_url)
    key_prefix = f"agentos-test-{uuid4().hex}"
    task_store = PostgresTaskStore(
        dsn=postgres_dsn,
        connection=_connect_postgres(postgres_dsn),
    )
    queue = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="pending-dispatch-recovery",
        allow_unscoped_consumers=True,
    )
    spawn_executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        message_queue=queue,
        task_store=task_store,
        spawn_executor=spawn_executor,
        subagent_factory=_StaticSubagentFactory(),
    )
    plan_store = InMemoryPlanStore()
    plan_store.create_plan(
        PlanState(
            plan_id="plan_pending_dispatch",
            objective="Recover planner assignment after scheduler restart.",
            owner_agent_id="parent",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_pending_dispatch",
                    instruction="Review recovered pending dispatch.",
                    status="assigned",
                    required_capabilities=("review",),
                    template_id="expert-reviewer",
                    task_id="task_pending_dispatch",
                    assigned_agent_id="expert",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_pending_dispatch",
                    step_id="step_pending_dispatch",
                    template_id="expert-reviewer",
                    task_id="task_pending_dispatch",
                    target_agent_id="expert",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
            created_at=9.0,
            updated_at=10.0,
        ),
    )
    planner = PlannerRuntime(
        store=plan_store,
        dispatcher=AgentCoordinatorPlanStepDispatcher(coordinator),
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review recovered planner work.",
                capabilities=("review",),
                target_agent_id="expert",
            ),
        ),
        clock=lambda: 20.0,
    )

    try:
        coordinator.attach_agent(
            AgentCard(
                agent_id="parent",
                name="Parent",
                description="Planner owner.",
                capabilities=("coordinate",),
            ),
            _build_agent_with_response("parent unused"),
        )
        coordinator.attach_agent(
            AgentCard(
                agent_id="expert",
                name="Expert",
                description="Live state-plane expert.",
                capabilities=("review",),
            ),
            _build_agent_with_response("expert recovered pending dispatch"),
        )

        report = planner.recover_pending_dispatches("plan_pending_dispatch")
        recovered_plan = planner.get_plan("plan_pending_dispatch")
        runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

        assert [assignment.task_id for assignment in report.assigned] == [
            "task_pending_dispatch",
        ]
        assert report.skipped == ()
        assert recovered_plan.assignments[0].dispatch_status == "submitted"
        assert recovered_plan.assignments[0].submitted_at == 20.0
        assert runner.run_once(timeout=1.0) is True

        results = coordinator.collect_results("parent")
        stored_task = task_store.get("task_pending_dispatch")
        assert len(results) == 1
        assert results[0].status == "completed"
        assert results[0].summary == "expert recovered pending dispatch"
        assert stored_task is not None
        assert stored_task.status == "completed"
        assert stored_task.result == results[0]
    finally:
        _cleanup_redis_agent_streams(redis_client, key_prefix)
        task_store.close()
        spawn_executor.shutdown()


def test_live_distributed_worker_reclaims_pending_task_request_after_crash() -> None:
    postgres_dsn, redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    redis_client = _connect_redis(redis_url)
    key_prefix = f"agentos-test-{uuid4().hex}"
    task_store = PostgresTaskStore(
        dsn=postgres_dsn,
        connection=_connect_postgres(postgres_dsn),
    )
    recovered_queue = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="recovered-worker",
        allow_unscoped_consumers=True,
    )
    crashed_queue = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="crashed-worker",
        allow_unscoped_consumers=True,
    )
    spawn_executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        message_queue=recovered_queue,
        task_store=task_store,
        spawn_executor=spawn_executor,
        subagent_factory=_StaticSubagentFactory(),
    )
    planner = PlannerRuntime(
        store=InMemoryPlanStore(),
        dispatcher=AgentCoordinatorPlanStepDispatcher(coordinator),
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review one planner step.",
                capabilities=("review",),
                target_agent_id="expert",
            ),
        ),
        id_factory=lambda prefix: {
            "plan": "plan_reclaim",
            "step": "step_reclaim",
            "task": "task_reclaim",
        }.get(str(prefix), f"{prefix}_reclaim"),
    )

    try:
        coordinator.attach_agent(
            AgentCard(
                agent_id="parent",
                name="Parent",
                description="Planner owner.",
                capabilities=("coordinate",),
            ),
            _build_agent_with_response("parent unused"),
        )
        coordinator.attach_agent(
            AgentCard(
                agent_id="expert",
                name="Expert",
                description="Live state-plane expert.",
                capabilities=("review",),
            ),
            _build_agent_with_response("expert recovered live work"),
        )
        plan = planner.create_plan(
            objective="Recover a pending Redis task delivery after worker crash.",
            owner_agent_id="parent",
        )
        plan = planner.add_step(
            plan.plan_id,
            instruction="Review after Redis pending reclaim.",
            required_capabilities=("review",),
            template_id="expert-reviewer",
        )
        dispatch_report = planner.dispatch_ready_steps(plan.plan_id)

        crashed_deliveries = crashed_queue.collect(
            "expert",
            envelope_types=("task_request",),
        )
        assert len(crashed_deliveries) == 1
        stored_before_reclaim = task_store.get(dispatch_report.assigned[0].task_id)
        assert stored_before_reclaim is not None
        assert stored_before_reclaim.status == "queued"

        reclaimed = recovered_queue.reclaim_pending(
            "expert",
            idle_threshold_ms=0,
            max_retries=3,
            envelope_types=("task_request",),
        )
        assert [delivery.envelope.payload.task_id for delivery in reclaimed] == [
            dispatch_report.assigned[0].task_id,
        ]
        recovered_queue.requeue("expert", reclaimed[0])

        runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")
        assert runner.run_once(timeout=1.0) is True

        results = coordinator.collect_results("parent")
        stored_task = task_store.get(dispatch_report.assigned[0].task_id)
        assert len(results) == 1
        assert results[0].status == "completed"
        assert results[0].summary == "expert recovered live work"
        assert stored_task is not None
        assert stored_task.status == "completed"
        assert stored_task.result == results[0]
    finally:
        _cleanup_redis_agent_streams(redis_client, key_prefix)
        task_store.close()
        spawn_executor.shutdown()
