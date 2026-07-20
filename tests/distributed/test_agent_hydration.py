from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos import AgentBuilder
from agentos.artifacts import (
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactValidationError,
)
from agentos.artifacts.types import ArtifactPage
from agentos.distributed.models import ArtifactContent
from agentos.providers import FakeProvider
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from tests.durable._fixtures import checkpoint_source
from tests.distributed.worker._fakes import SCOPE, claimed_execution
from tests.planning._async import async_test


ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"


class _BoundStateStore:
    def __init__(self, checkpoint: object | None) -> None:
        self.loaded: list[str] = []
        self.checkpoint = checkpoint

    async def load_checkpoint(self, session_id: str):  # type: ignore[no-untyped-def]
        self.loaded.append(session_id)
        return self.checkpoint


class _StateStore:
    def __init__(self, checkpoint: object | None = None) -> None:
        self.bindings: list[tuple[object, _BoundStateStore]] = []
        self.checkpoint = checkpoint

    def bind(self, scope):  # type: ignore[no-untyped-def]
        bound = _BoundStateStore(self.checkpoint)
        self.bindings.append((scope, bound))
        return bound


class _Artifacts:
    def __init__(self) -> None:
        self.record = ArtifactRecord(
            ARTIFACT_ID,
            "session_1",
            "drawing.png",
            "image/png",
            7,
            datetime(2026, 7, 20, tzinfo=UTC),
        )
        self.read_scopes: list[object] = []

    async def read(self, *, scope, session_id, artifact_id):  # type: ignore[no-untyped-def]
        assert session_id == "session_1"
        assert artifact_id == ARTIFACT_ID
        self.read_scopes.append(scope)
        return ArtifactContent(self.record, b"drawing")

    async def list(self, *, scope, session_id, cursor, limit):  # type: ignore[no-untyped-def]
        assert scope == SCOPE
        assert session_id == "session_1"
        assert cursor is None
        assert limit == 20
        return ArtifactPage((self.record,), None)


class _ResumeValidator:
    async def validate(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs


@async_test
async def test_claim_hydration_binds_tenant_and_uses_read_only_shared_artifacts() -> None:
    state = _StateStore()
    artifacts = _Artifacts()
    factory = ClaimScopedAgentFactory(
        builder=AgentBuilder().provider(FakeProvider([])),
        state_store=state,  # type: ignore[arg-type]
        artifact_store=artifacts,  # type: ignore[arg-type]
        side_effect_store=InMemorySideEffectStore(),
        side_effect_resume_validator=_ResumeValidator(),
    )
    claimed = claimed_execution()

    first = await factory.hydrate(claimed=claimed)
    second = await factory.hydrate(claimed=claimed)

    assert first is not second
    assert first.query_loop is not second.query_loop
    assert [scope for scope, _ in state.bindings] == [SCOPE, SCOPE]
    assert [bound.loaded for _, bound in state.bindings] == [
        ["session_1"],
        ["session_1"],
    ]
    assert first.query_loop.tool_payload_runtime.context.tenant_id == "tenant_1"
    assert first.query_loop.checkpoint_store is state.bindings[0][1]
    assert first.query_loop.side_effect_store is factory.side_effect_store
    assert (
        first.query_loop.side_effect_resume_validator
        is factory.side_effect_resume_validator
    )
    assert await first.artifacts.list() == ArtifactPage((artifacts.record,), None)
    assert await first.artifacts.read(ARTIFACT_ID) == b"drawing"
    assert artifacts.read_scopes == [SCOPE]

    with pytest.raises(ArtifactValidationError, match="read-only"):
        await first.artifacts.upload(
            data=b"new",
            filename="new.png",
            media_type="image/png",
        )


@async_test
async def test_claim_artifact_mount_reads_blob_once_and_reuses_claim_cache() -> None:
    state = _StateStore()
    artifacts = _Artifacts()
    factory = ClaimScopedAgentFactory(
        builder=AgentBuilder().provider(FakeProvider([])),
        state_store=state,  # type: ignore[arg-type]
        artifact_store=artifacts,  # type: ignore[arg-type]
        side_effect_store=InMemorySideEffectStore(),
        side_effect_resume_validator=_ResumeValidator(),
    )
    agent = await factory.hydrate(claimed=claimed_execution())

    await agent.artifacts.mount_user_upload(ARTIFACT_ID)
    await agent.artifacts.prepare_projection_cache()

    assert artifacts.read_scopes == [SCOPE]
    with pytest.raises(ArtifactNotFoundError):
        await agent.artifacts._store.get("session_other", ARTIFACT_ID)
    assert artifacts.read_scopes == [SCOPE]


def test_claim_hydration_rejects_static_context_projections() -> None:
    class _Projection:
        def projections(self):  # type: ignore[no-untyped-def]
            return ()

    builder = AgentBuilder().provider(FakeProvider([])).context_projections(
        (_Projection(),),
    )

    with pytest.raises(ValueError, match="context projections"):
        ClaimScopedAgentFactory(
            builder=builder,
            state_store=_StateStore(),  # type: ignore[arg-type]
            artifact_store=_Artifacts(),  # type: ignore[arg-type]
            side_effect_store=InMemorySideEffectStore(),
            side_effect_resume_validator=_ResumeValidator(),
        )


@async_test
async def test_claim_hydration_restores_the_canonical_checkpoint() -> None:
    checkpoint = checkpoint_source("session_1").capture()
    state = _StateStore(checkpoint)
    factory = ClaimScopedAgentFactory(
        builder=AgentBuilder().provider(FakeProvider([])),
        state_store=state,  # type: ignore[arg-type]
        artifact_store=_Artifacts(),  # type: ignore[arg-type]
        side_effect_store=InMemorySideEffectStore(),
        side_effect_resume_validator=_ResumeValidator(),
    )

    agent = await factory.hydrate(claimed=claimed_execution())

    messages = agent.query_loop.message_runtime.store.all()
    assert [message.content for message in messages] == ["question"]
    assert agent.query_loop.context_runtime.snapshot().working_state["task_goal"] == (
        "resume after restart"
    )
