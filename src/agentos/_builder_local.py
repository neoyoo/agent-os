from __future__ import annotations

from collections.abc import Iterable
from agentos.artifacts import ArtifactRuntime, InMemoryArtifactStore
from agentos._builder_state import RuntimeStateComponents
from agentos.artifacts.projection import (
    ArtifactCatalogProjectionProvider,
    ArtifactMountProjectionProvider,
)
from agentos.context import (
    ContextProjectionProvider,
    ContextProjectionRegistry,
    ContextRuntime,
    ContextSnapshotRenderer,
)
from agentos.context.projection_registry import ContextRuntimeProjectionProvider
from agentos.events import EventBus
from agentos.messages import MessageRuntime
from agentos.providers import ProviderToolSpec
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuilder,
    SystemEnvelopeRenderer,
)
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime
from agentos.runtime.session import SessionState
from agentos.tokens import TokenCounter


def assemble_local_state(
    *,
    session_id: str,
    context: ContextRuntime | None,
    event_bus: EventBus | None,
) -> RuntimeStateComponents:
    resolved_context = context or ContextRuntime(event_bus=event_bus)
    if resolved_context.session_id not in (None, session_id):
        raise ValueError("context runtime belongs to another session")
    resolved_context.session_id = session_id
    return RuntimeStateComponents(
        context=resolved_context,
        artifacts=ArtifactRuntime(
            session_id=session_id,
            store=InMemoryArtifactStore(),
            event_bus=event_bus,
        ),
        runs=RunRuntime(session_id=session_id, store=InMemoryRunStore()),
        session=SessionState(id=session_id),
    )


def assemble_provider_request_builder(
    *,
    renderer: SystemEnvelopeRenderer,
    messages: MessageRuntime,
    tools: list[ProviderToolSpec],
    token_counter: TokenCounter,
    state: RuntimeStateComponents,
    extension_projections: Iterable[ContextProjectionProvider] = (),
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=renderer,
        message_runtime=messages,
        tools=tools,
        parallel_tool_calls=True,
        snapshot_renderer=ContextSnapshotRenderer(token_counter),
        context_projections=ContextProjectionRegistry(
            (
                ContextRuntimeProjectionProvider(state.context),
                ArtifactCatalogProjectionProvider(state.artifacts),
                *tuple(extension_projections),
            )
        ),
        input_projections=(ArtifactMountProjectionProvider(state.artifacts),),
    )
