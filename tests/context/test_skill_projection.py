from __future__ import annotations

import asyncio

import pytest

from agentos.capabilities.skills import (
    BoundSkillInstructionProvider,
    BoundSkillProjectionProvider,
    BoundSkillTools,
    BuiltinSkillSource,
    SkillContentSource,
    SkillDefinition,
    SkillDescriptor,
    SkillLoadResult,
    SkillMetadata,
    SkillRegistry,
    SkillResourceLoadResult,
    SkillResourceRef,
    SkillRuntime,
    SkillTrustDecision,
    SkillTrustError,
    SkillVerificationSubject,
)
from agentos.context.snapshot import ContextSnapshotRenderer
from tests.context._snapshot_fixtures import RecordingTokenCounter


class MutableTrustPolicy:
    def __init__(self, *, verified: bool = True) -> None:
        self.verified = verified

    def verify(self, metadata, subject):  # type: ignore[no-untyped-def]
        return SkillTrustDecision(
            verified=self.verified and metadata.trust == "trusted",
            policy_id="tests",
            subject=subject,
        )


class MutableRevisionSource(SkillContentSource):
    def __init__(self) -> None:
        self.revision = "1"
        self.descriptor = SkillDescriptor(
            SkillMetadata("review", "Review code.", True, "trusted"),
            "Review code.",
        )

    async def list_skills(self) -> list[SkillDescriptor]:
        return [self.descriptor]

    async def load_skill(self, name: str) -> SkillLoadResult:
        content = f"# Review\nrevision={self.revision}"
        return SkillLoadResult(
            name=name,
            content=content,
            metadata=self.descriptor.metadata,
            subject=SkillVerificationSubject.from_content(
                source_id="mutable",
                skill_name=name,
                source_revision=self.revision,
                content=content,
            ),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        return ()

    async def load_resource(self, name: str, path: str) -> SkillResourceLoadResult:
        raise KeyError(path)


def definition(*, trust: str = "trusted") -> SkillDefinition:
    return SkillDefinition(
        name="review",
        description='Review <code> & report "findings".',
        when_to_use="Review code.",
        content="# Review\nFind bugs before summaries.",
        source="builtin",
        trust=trust,  # type: ignore[arg-type]
    )


async def runtime_for(
    skill: SkillDefinition,
    policy: MutableTrustPolicy,
) -> SkillRuntime:
    registry = await SkillRegistry.aload(BuiltinSkillSource((skill,)))
    return SkillRuntime(registry, policy)


def test_verified_trusted_skill_is_session_scoped_and_projects_only_metadata() -> None:
    policy = MutableTrustPolicy()

    async def run() -> tuple[SkillRuntime, str]:
        runtime = await runtime_for(definition(), policy)
        result = await runtime.load("session-a", "review")
        return runtime, result

    runtime, result = asyncio.run(run())

    assert result == ("Skill 已加载：review。可信指令将在下一次模型请求中生效。")
    assert runtime.items("session-a")[0].text.startswith("# Review")
    assert runtime.items("session-b") == ()

    projections = BoundSkillProjectionProvider(runtime, "session-a").projections()
    snapshot = ContextSnapshotRenderer(RecordingTokenCounter(1)).render(projections)
    assert 'name="review"' in snapshot.xml
    assert (
        'description="Review &lt;code&gt; &amp; report &quot;findings&quot;."'
        in snapshot.xml
    )
    assert 'loadable="true"' in snapshot.xml
    assert 'trust="trusted"' in snapshot.xml
    assert "Find bugs before summaries" not in snapshot.xml


def test_builtin_is_not_trusted_without_verified_policy_decision() -> None:
    async def run() -> None:
        runtime = await runtime_for(definition(), MutableTrustPolicy(verified=False))
        with pytest.raises(SkillTrustError, match="skill trust verification failed"):
            await runtime.load("session-a", "review")

    asyncio.run(run())


@pytest.mark.parametrize("result_kind", ["wrong-type", "forged-decision"])
def test_invalid_trust_policy_result_fails_closed(result_kind: str) -> None:
    class InvalidPolicy:
        def verify(self, metadata, subject):  # type: ignore[no-untyped-def]
            if result_kind == "wrong-type":
                return object()
            decision = SkillTrustDecision(True, "tests", subject)
            object.__setattr__(decision, "verified", "yes")
            return decision

    async def run() -> None:
        runtime = await runtime_for(definition(), InvalidPolicy())  # type: ignore[arg-type]
        with pytest.raises(SkillTrustError, match="skill trust verification failed"):
            await runtime.load("session-a", "review")

    asyncio.run(run())


def test_untrusted_skill_body_stays_in_bounded_tool_result() -> None:
    async def run() -> tuple[SkillRuntime, str]:
        runtime = await runtime_for(definition(trust="untrusted"), MutableTrustPolicy())
        result = await runtime.load("session-a", "review")
        return runtime, result

    runtime, result = asyncio.run(run())

    assert "# Review" in result
    assert runtime.items("session-a") == ()


def test_policy_revocation_and_disable_remove_trusted_instruction() -> None:
    policy = MutableTrustPolicy()

    async def load() -> SkillRuntime:
        runtime = await runtime_for(definition(), policy)
        await runtime.load("session-a", "review")
        return runtime

    runtime = asyncio.run(load())
    policy.verified = False
    assert BoundSkillInstructionProvider(runtime, "session-a").items() == ()

    policy.verified = True
    asyncio.run(runtime.load("session-a", "review"))
    runtime.disable("session-a", "review")
    assert runtime.items("session-a") == ()


def test_activation_snapshot_remains_version_pinned_until_explicit_reload() -> None:
    policy = MutableTrustPolicy()

    async def run() -> SkillRuntime:
        source = MutableRevisionSource()
        runtime = SkillRuntime(await SkillRegistry.aload(source), policy)
        await runtime.load("session-a", "review")
        source.revision = "2"
        return runtime

    runtime = asyncio.run(run())

    assert runtime.items("session-a")[0].text.endswith("revision=1")


def test_explicit_reload_invalidates_version_pinned_activation_before_failure() -> None:
    policy = MutableTrustPolicy()

    async def run() -> SkillRuntime:
        source = MutableRevisionSource()
        runtime = SkillRuntime(await SkillRegistry.aload(source), policy)
        await runtime.load("session-a", "review")
        source.revision = "2"
        policy.verified = False
        with pytest.raises(SkillTrustError):
            await runtime.load("session-a", "review")
        return runtime

    runtime = asyncio.run(run())

    assert runtime.items("session-a") == ()


def test_bound_skill_tools_capture_session_without_exposing_it_in_schema() -> None:
    policy = MutableTrustPolicy()

    async def run() -> tuple[SkillRuntime, tuple[object, ...]]:
        runtime = await runtime_for(definition(), policy)
        tools = BoundSkillTools(runtime, "session-a").registered_tools()
        load_tool = tools[0]
        result = await load_tool.handler({"skill_name": "review"})  # type: ignore[misc]
        assert result.startswith("Skill 已加载")
        return runtime, tools

    runtime, tools = asyncio.run(run())

    assert [tool.name for tool in tools] == [
        "load_skill",
        "disable_skill",
        "load_skill_resource",
    ]
    assert all("session_id" not in tool.parameters["properties"] for tool in tools)
    assert runtime.items("session-a")
    assert runtime.items("session-b") == ()


def test_close_session_clears_all_activations() -> None:
    async def run() -> SkillRuntime:
        runtime = await runtime_for(definition(), MutableTrustPolicy())
        await runtime.load("session-a", "review")
        runtime.close_session("session-a")
        return runtime

    assert asyncio.run(run()).items("session-a") == ()
