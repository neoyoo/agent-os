from __future__ import annotations

import asyncio

import pytest

from agentos import AgentBuilder
from agentos._json_values import freeze_json_mapping
from agentos.capabilities import InlineToolResultRef, SideEffectPolicy
from agentos.capabilities.executor import ToolExecutionError
from agentos.providers import ProviderRequest, ProviderResponse, TextPart
from agentos.runtime import AgentResult
from agentos.runtime.payloads import PayloadProtectionContext, protect_payload
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
from tests.integration._cancel_recovery_support import ScriptedProvider
from tests.integration._side_effect_resolution_support import (
    open_reconciliation_scenario,
    scenario_agent_factory,
    submit_resolution,
)
from tests.integration._side_effect_resolution_truth import (
    assert_resolution_terminal_truth,
    resolution_records,
)


pytestmark = pytest.mark.integration


class _UnexpectedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise AssertionError("resolution must not call the provider")


class _RecordingProvider:
    def __init__(self, response: ProviderResponse) -> None:
        self._response = response
        self.requests: list[ProviderRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return self._response


def test_live_accept_result_reuses_resolution_without_handler_retry() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_accept_result())


async def _verify_accept_result() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.NON_RETRYABLE)
    result_ref = InlineToolResultRef("accepted external result")
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.ACCEPT_RESULT,
        result_ref=result_ref,
        result_digest=result_ref_digest(result_ref),
    )
    try:
        claimed = await submit_resolution(scenario, resolution)
        provider = _RecordingProvider(
            ProviderResponse("accepted result completed"),
        )
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=AgentBuilder().provider(provider).tools([scenario.tool]),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        outcome = await agent.run(claimed.claimed.execution)

        assert outcome == AgentResult("accepted result completed")
        assert provider.calls == 1
        assistant = tuple(
            item
            for item in provider.requests[0].messages
            if any(call.id == "call_unsafe" for call in item.tool_calls)
        )
        tool_result = tuple(
            item
            for item in provider.requests[0].messages
            if item.kind == "tool_result" and item.tool_call_id == "call_unsafe"
        )
        assert len(assistant) == 1
        assert len(tool_result) == 1
        assert assistant[0].role == "assistant"
        assert assistant[0].tool_calls[0].name == "unsafe_tool"
        assert tool_result[0].role == "tool"
        assert tool_result[0].persistence == "stored"
        assert tool_result[0].authority == "tool_data"
        assert tool_result[0].content == (TextPart("accepted external result"),)
        assert scenario.probe.handler_attempts == [1]
        records = await resolution_records(scenario)
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.RESOLVED
        assert records[0].resolution is SideEffectResolutionOutcome.ACCEPTED
        assert records[0].result_ref == result_ref
        await assert_resolution_terminal_truth(
            scenario,
            "completed",
            source_consumed=True,
        )
    finally:
        await scenario.close()


def test_live_retry_proven_safe_executes_only_the_new_attempt() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_retry_proven_safe())


async def _verify_retry_proven_safe() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.NON_RETRYABLE)
    attestation = protect_payload(
        scenario.protector,
        freeze_json_mapping({"approved": True}),
        context=PayloadProtectionContext(
            scenario.scope.tenant_id,
            scenario.session_id,
        ),
    )
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.RETRY_PROVEN_SAFE,
        attestation_ref=attestation,
        attestation_digest=attestation.digest,
    )
    try:
        claimed = await submit_resolution(scenario, resolution)
        provider = ScriptedProvider([ProviderResponse("safe retry completed")])
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=AgentBuilder().provider(provider).tools([scenario.tool]),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        outcome = await agent.run(claimed.claimed.execution)

        assert outcome == AgentResult("safe retry completed")
        assert provider.calls == 1
        assert scenario.probe.handler_attempts == [1, 2]
        records = await resolution_records(scenario)
        assert len(records) == 2
        assert records[0].status is SideEffectStatus.RESOLVED
        assert records[0].resolution is SideEffectResolutionOutcome.RETRY_SAFE
        assert records[0].attestation_ref == attestation
        assert records[1].attempt_id.attempt == 2
        assert records[1].status is SideEffectStatus.COMPLETED
        await assert_resolution_terminal_truth(
            scenario,
            "completed",
            source_consumed=True,
        )
    finally:
        await scenario.close()


def test_live_fail_resolution_commits_failed_without_provider_or_handler() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_fail_resolution())


async def _verify_fail_resolution() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.NON_RETRYABLE)
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.FAIL,
    )
    try:
        claimed = await submit_resolution(scenario, resolution)
        provider = _UnexpectedProvider()
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=AgentBuilder().provider(provider).tools([scenario.tool]),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        with pytest.raises(
            ToolExecutionError,
            match="side effect reconciliation failed the run",
        ):
            await agent.run(claimed.claimed.execution)

        assert provider.calls == 0
        assert scenario.probe.handler_attempts == [1]
        records = await resolution_records(scenario)
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.RESOLVED
        assert records[0].resolution is SideEffectResolutionOutcome.FAILED
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
    finally:
        await scenario.close()


def test_live_compensate_resolution_commits_effect_then_failed_terminal() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_compensate_resolution())


async def _verify_compensate_resolution() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.COMPENSATABLE)
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.COMPENSATE,
    )
    try:
        claimed = await submit_resolution(scenario, resolution)
        provider = _UnexpectedProvider()
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=AgentBuilder().provider(provider).tools([scenario.tool]),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        with pytest.raises(
            ToolExecutionError,
            match="compensated side effect failed the run",
        ):
            await agent.run(claimed.claimed.execution)

        assert provider.calls == 0
        assert scenario.probe.handler_attempts == [1]
        assert len(scenario.probe.compensation_invocations) == 1
        assert scenario.probe.compensation_effects == set(
            scenario.probe.compensation_invocations,
        )
        records = await resolution_records(scenario)
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.COMPENSATED
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
    finally:
        await scenario.close()
