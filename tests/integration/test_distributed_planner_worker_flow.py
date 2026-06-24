from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    ExpertAgentRunner,
    InMemoryPlanStore,
    InMemoryRegistry,
    PlannerRuntime,
    SpawnExecutor,
    SubagentInitRequest,
    SubAgentTemplate,
)
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.redis_queue import RedisAgentMessageQueue
from agentos.context import ContextRenderer, ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder


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
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": FakeProvider([ProviderResponse(content=content)]),
        },
    )


class _StaticSubagentFactory:
    def create_subagent(self, request: SubagentInitRequest) -> Agent:
        return _build_agent_with_response("child result")


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
        coordinator=coordinator,
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
        redis_client.delete(
            f"{key_prefix}:multi:inbox:parent",
            f"{key_prefix}:multi:inbox:parent:task_request",
            f"{key_prefix}:multi:inbox:parent:task_result",
            f"{key_prefix}:multi:inbox:parent:team_message",
            f"{key_prefix}:multi:inbox:expert",
            f"{key_prefix}:multi:inbox:expert:task_request",
            f"{key_prefix}:multi:inbox:expert:task_result",
            f"{key_prefix}:multi:inbox:expert:team_message",
        )
        task_store.close()
        spawn_executor.shutdown()
