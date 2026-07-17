from __future__ import annotations

import asyncio
from collections.abc import Callable

from agentos import Agent
from agentos.context import (
    ContextProjectionRegistry,
    ContextRuntime,
    ContextSnapshotRenderer,
    WorkingStateField,
)
from agentos.context.models import ContextSlotProjection, ProjectionVariant
from agentos.context.projection_registry import ContextRuntimeProjectionProvider
from agentos.context.xml import XmlElement
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderRequest, ProviderResponse, TextPart
from agentos.runtime import (
    LocalContinuationInput,
    ProviderRequestBuilder,
    QueryLoop,
    RetryPolicy,
)
from agentos.runtime.continuation import ContinuationNotice
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


class MutableSkillProjectionProvider:
    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        self.calls = 0

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        self.calls += 1
        return (
            ContextSlotProjection(
                slot="available-skills",
                owner="SkillRuntime",
                variants=(
                    ProjectionVariant(
                        XmlElement(
                            "available-skills",
                            (("truncated", "false"),),
                            children=(
                                XmlElement(
                                    "skill",
                                    (
                                        ("name", self.skill_name),
                                        ("description", "Authoritative skill state"),
                                        ("loadable", "true"),
                                        ("trust", "trusted"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )


class RetryMutatingProvider:
    def __init__(self, update_authority: Callable[[], None]) -> None:
        self._update_authority = update_authority
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            self._update_authority()
            raise RuntimeError("retry after authoritative state update")
        return ProviderResponse(content="recovered")


class MutableNoticeProvider:
    def __init__(self) -> None:
        self.notices: tuple[ContinuationNotice, ...] = ()
        self.calls = 0

    def consume_notices(self) -> tuple[ContinuationNotice, ...]:
        self.calls += 1
        notices = self.notices
        self.notices = ()
        return notices


def _context_with_goal(goal: str) -> ContextRuntime:
    context = ContextRuntime()
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
                purpose="Current authoritative task goal.",
            ),
        ],
    )
    context.update_state("task_goal", goal)
    return context


def _agent(
    *,
    context: ContextRuntime,
    skills: MutableSkillProjectionProvider,
    provider: object,
    notices: MutableNoticeProvider | None = None,
    retry_policy: RetryPolicy | None = None,
) -> Agent:
    messages = MessageRuntime()
    token_counter = HeuristicTokenCounter()
    request_builder = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        snapshot_renderer=ContextSnapshotRenderer(token_counter),
        context_projections=ContextProjectionRegistry(
            (
                ContextRuntimeProjectionProvider(context),
                skills,
            ),
        ),
    )
    return Agent(
        QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=request_builder,
            provider=provider,  # type: ignore[arg-type]
            turn_notice_provider=notices,
            retry_policy=retry_policy,
            token_counter=token_counter,
        ),
    )


def _snapshot_xml(request: ProviderRequest) -> str:
    snapshot = request.messages[0]
    assert snapshot.kind == "context_snapshot"
    assert len(snapshot.content) == 1
    text = snapshot.content[0]
    assert isinstance(text, TextPart)
    return text.text


def _business_message_texts(request: ProviderRequest) -> list[str]:
    texts: list[str] = []
    for item in request.messages:
        if item.kind != "business_message":
            continue
        assert len(item.content) == 1
        part = item.content[0]
        assert isinstance(part, TextPart)
        texts.append(part.text)
    return texts


def test_provider_retry_rebuilds_context_and_extension_projections() -> None:
    async def run() -> None:
        context = _context_with_goal("goal-before-retry")
        skills = MutableSkillProjectionProvider("skill-before-retry")

        def update_authority() -> None:
            context.update_state("task_goal", "goal-after-retry")
            skills.skill_name = "skill-after-retry"

        provider = RetryMutatingProvider(update_authority)
        agent = _agent(
            context=context,
            skills=skills,
            provider=provider,
            retry_policy=RetryPolicy(max_retries=1, backoff_base=0, jitter=0),
        )

        result = await agent.run("inspect current authority")

        assert result.content == "recovered"
        assert len(provider.requests) == 2
        assert provider.requests[0] is not provider.requests[1]
        first_snapshot = _snapshot_xml(provider.requests[0])
        retry_snapshot = _snapshot_xml(provider.requests[1])
        assert "goal-before-retry" in first_snapshot
        assert 'name="skill-before-retry"' in first_snapshot
        assert "goal-after-retry" in retry_snapshot
        assert 'name="skill-after-retry"' in retry_snapshot
        assert "goal-before-retry" not in retry_snapshot
        assert 'name="skill-before-retry"' not in retry_snapshot
        assert skills.calls == 2

    asyncio.run(run())


def test_continuation_rebuilds_from_latest_context_and_message_truth() -> None:
    async def run() -> None:
        context = _context_with_goal("goal-before-continuation")
        skills = MutableSkillProjectionProvider("skill-before-continuation")
        notices = MutableNoticeProvider()
        provider = FakeProvider(
            [
                ProviderResponse(content="initial answer"),
                ProviderResponse(content="continuation answer"),
            ],
        )
        agent = _agent(
            context=context,
            skills=skills,
            provider=provider,
            notices=notices,
        )

        first_result = await agent.run("initial request")
        assert first_result.content == "initial answer"
        context.update_state("task_goal", "goal-after-continuation")
        skills.skill_name = "skill-after-continuation"
        notices.notices = (
            ContinuationNotice(
                kind="task_completed",
                subject_id="task_latest",
                action="check_agent_tasks",
            ),
        )

        continuation_result = await agent.run(LocalContinuationInput())

        assert continuation_result.content == "continuation answer"
        assert len(provider.requests) == 2
        first_request, continuation_request = provider.requests
        assert continuation_request is not first_request
        first_snapshot = _snapshot_xml(first_request)
        continuation_snapshot = _snapshot_xml(continuation_request)
        assert "goal-before-continuation" in first_snapshot
        assert 'name="skill-before-continuation"' in first_snapshot
        assert "goal-after-continuation" in continuation_snapshot
        assert 'name="skill-after-continuation"' in continuation_snapshot
        assert "goal-before-continuation" not in continuation_snapshot
        assert 'name="skill-before-continuation"' not in continuation_snapshot
        assert _business_message_texts(first_request) == ["initial request"]
        assert _business_message_texts(continuation_request) == [
            "initial request",
            "initial answer",
        ]
        assert [item.kind for item in continuation_request.messages][-1] == (
            "continuation_data"
        )
        assert notices.calls == 1
        assert skills.calls == 2

    asyncio.run(run())
