from __future__ import annotations

from datetime import UTC, datetime

from agentos.artifacts import ArtifactRecord
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
)
from agentos.distributed._tool_result_artifacts import (
    DistributedToolResultRefProjector,
)
from agentos.distributed.models import RequestScope
from agentos.policies import ToolResultBudget
from agentos.providers import ProviderToolCall
from agentos.runtime.tool_invocations import build_tool_invocation_plan
from agentos.tokens import HeuristicTokenCounter
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"


class _Artifacts:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []

    async def upload(self, **kwargs: object) -> ArtifactRecord:
        self.uploads.append(kwargs)
        return ArtifactRecord(
            ARTIFACT_ID,
            "session_1",
            "tool-result.txt",
            "text/plain",
            len(kwargs["data"]),  # type: ignore[arg-type]
            datetime(2026, 7, 20, tzinfo=UTC),
        )


def _invocation():  # type: ignore[no-untyped-def]
    return build_tool_invocation_plan(
        tenant_id=SCOPE.tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "read_large_file", {}),),
    ).entries[0].invocation


@async_test
async def test_oversized_result_is_uploaded_before_artifact_ref_is_returned() -> None:
    artifacts = _Artifacts()
    projector = DistributedToolResultRefProjector(
        scope=SCOPE,
        session_id="session_1",
        artifacts=artifacts,  # type: ignore[arg-type]
        budget=ToolResultBudget(default_max_tokens=5),
        token_counter=HeuristicTokenCounter(char_per_token=1),
    )

    reference = await projector.project(_invocation(), "x" * 100)

    assert isinstance(reference, ArtifactToolResultRef)
    assert reference.artifact.artifact_id == ARTIFACT_ID
    assert "tool result omitted" in reference.preview
    assert artifacts.uploads == [
        {
            "scope": SCOPE,
            "session_id": "session_1",
            "upload_id": (
                f"tool-result:{_invocation().context.operation_id}:1"
            ),
            "data": b"x" * 100,
            "filename": "tool-result.txt",
            "media_type": "text/plain",
        }
    ]


@async_test
async def test_budgeted_result_remains_inline_without_artifact_io() -> None:
    artifacts = _Artifacts()
    projector = DistributedToolResultRefProjector(
        scope=SCOPE,
        session_id="session_1",
        artifacts=artifacts,  # type: ignore[arg-type]
        budget=ToolResultBudget(default_max_tokens=5),
        token_counter=HeuristicTokenCounter(char_per_token=1),
    )

    reference = await projector.project(_invocation(), "small")

    assert reference == InlineToolResultRef("small")
    assert artifacts.uploads == []
