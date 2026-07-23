from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentos import AgentBuilder
from agentos.providers import FakeProvider
from agentos.runtime import LocalRuntimeProfile, QueryLoop
from agentos.workspace import LocalWorkspaceProvider, WorkspaceRequest


def test_local_runtime_profile_builds_unified_agent() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
    )

    agent = profile.build_agent()

    assert profile.name == "local"
    assert isinstance(agent.query_loop, QueryLoop)
    assert asyncio.run(agent.run("hello")).content == "ok"


def test_local_runtime_profile_forwards_explicit_session_id() -> None:
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
    )

    agent = profile.build_agent("session_profile")

    assert agent.query_loop.session_state.id == "session_profile"
    assert agent.query_loop.context_runtime.session_id == "session_profile"
    assert agent.artifacts.session_id == "session_profile"


def test_local_runtime_profile_rejects_removed_mode_selector() -> None:
    removed_selector = "loop" + "_mode"
    arguments = {
        "agent_builder": AgentBuilder().provider(FakeProvider(["ok"])),
        removed_selector: "async",
    }

    with pytest.raises(TypeError, match=removed_selector):
        LocalRuntimeProfile(**arguments)  # type: ignore[arg-type]


def test_local_runtime_profile_resolves_process_workspace(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider(base_dir=tmp_path)
    profile = LocalRuntimeProfile(
        agent_builder=AgentBuilder().provider(FakeProvider(["ok"])),
        workspace_provider=provider,
        workspace_request=WorkspaceRequest(
            agent_id="agent_a",
            requested_scope="process",
        ),
    )

    assert profile.workspace_handle is not None
    assert profile.workspace_handle.scope == "process"
    assert profile.workspace_handle.root == str(tmp_path)
