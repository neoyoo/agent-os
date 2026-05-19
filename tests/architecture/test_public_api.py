import importlib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_public_package_imports_as_agentos_pep8_name() -> None:
    package = importlib.import_module("agentos")

    assert package.__version__ == "0.1.0"


def test_legacy_mixed_case_package_name_is_not_public_api() -> None:
    legacy_name = "agent" + "Os"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_name)


def test_public_api_uses_responsibility_specific_names() -> None:
    agentos = importlib.import_module("agentos")
    runtime = importlib.import_module("agentos.runtime")
    capabilities = importlib.import_module("agentos.capabilities")
    hooks = importlib.import_module("agentos.hooks")
    providers = importlib.import_module("agentos.providers")

    assert hasattr(runtime, "QueryLoop")
    assert hasattr(runtime, "ProviderRequestBuilder")
    assert hasattr(runtime, "TurnNoticeProvider")
    assert not hasattr(runtime, "AgentLoop")
    assert not hasattr(runtime, "RequestBuilder")
    assert not hasattr(runtime, "RuntimeEvent")

    assert hasattr(capabilities, "ToolCallRouter")
    assert not hasattr(capabilities, "CapabilityRuntime")

    assert hasattr(hooks, "HookManager")
    assert not hasattr(hooks, "HookRuntime")

    assert hasattr(providers, "Provider")
    assert not hasattr(providers, "ProviderRuntime")

    assert hasattr(agentos, "QueryLoop")
    assert hasattr(agentos, "ProviderRequestBuilder")
    assert hasattr(agentos, "Provider")
    assert hasattr(agentos, "ToolCallRouter")
    assert hasattr(agentos, "HookManager")


def test_context_protocol_public_constants_remain_available() -> None:
    context_protocol = importlib.import_module("agentos.context_protocol")

    assert hasattr(context_protocol, "CONTEXT_PROTOCOL_TOOL_DEFINITIONS")
    assert hasattr(context_protocol, "CONTEXT_PROTOCOL_TOOL_NAMES")
    assert hasattr(context_protocol, "context_protocol_tool_specs")
    assert context_protocol.CONTEXT_PROTOCOL_TOOL_NAMES == {
        "declare_schema",
        "update_state",
        "extend_schema",
        "start_chapter",
        "recall_context",
        "load_image",
    }


def test_phase5_phase6_public_api_exports() -> None:
    capabilities = importlib.import_module("agentos.capabilities")
    events = importlib.import_module("agentos.events")
    persistence = importlib.import_module("agentos.persistence")
    observability = importlib.import_module("agentos.observability")

    for name in [
        "FileSystemSkillSource",
        "SkillContentSource",
        "SkillDefinition",
        "SkillRegistry",
        "SkillLoadResult",
        "SkillResourceLoadResult",
        "SkillResourceRef",
        "MCPToolInfo",
        "MCPClient",
        "MCPRegistry",
        "MCPToolAdapter",
    ]:
        assert hasattr(capabilities, name)

    for name in [
        "AgentEvent",
        "EventBus",
        "EventSubscriber",
        "TurnStartedEvent",
    ]:
        assert hasattr(events, name)

    for name in [
        "SessionSnapshot",
        "SessionPersistence",
        "MemoryPersistence",
        "FileSystemPersistence",
        "PostgresDurableSessionStore",
        "SQLitePersistence",
        "SnapshotLoadError",
    ]:
        assert hasattr(persistence, name)

    for name in [
        "CapturePolicy",
        "EventLog",
        "EventRecord",
        "InMemoryTracer",
        "NoOpTracer",
        "ObservabilityContext",
        "ObservabilityConfig",
        "create_langfuse_otel_tracer",
        "create_otel_tracer",
        "current_observability_context",
        "inject_trace_headers",
        "instrument_query_loop",
        "use_observability_context",
    ]:
        assert hasattr(observability, name)

    for removed_name in [
        "TraceRecord",
        "TraceSink",
        "EventTraceProjector",
        "OTelAdapter",
        "LangfuseAdapter",
    ]:
        assert not hasattr(observability, removed_name)

    assert observability.EventSubscriber is events.EventSubscriber


def test_phase7_memory_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    memory = importlib.import_module("agentos.memory")

    for name in [
        "CompressedSegmentPackage",
        "DurableSessionStore",
        "HotSessionState",
        "HotSessionStore",
        "MemoryRuntime",
        "QdrantRecallIndex",
        "RecallCandidate",
        "RecallIndex",
        "RedisHotSessionStore",
        "SegmentRecallDocument",
        "TextEmbeddingProvider",
    ]:
        assert hasattr(memory, name)

    for name in [
        "CompressedSegmentPackage",
        "MemoryRuntime",
        "PostgresDurableSessionStore",
        "QdrantRecallIndex",
        "RedisHotSessionStore",
        "SegmentRecallDocument",
    ]:
        assert hasattr(agentos, name)


def test_phase8_multi_agent_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    multi = importlib.import_module("agentos.multi")

    for name in [
        "AgentCard",
        "AgentCoordinator",
        "AgentCoordinationTools",
        "AgentEnvelope",
        "AgentInbox",
        "AgentInboxFullError",
        "AgentInboxMissingError",
        "AgentTaskNoticeStore",
        "ContinuationErrorRecord",
        "ContinuationTrigger",
        "ExpertAgentRunner",
        "InMemoryRegistry",
        "LocalContinuationTrigger",
        "SpawnExecutor",
        "SubagentInitRequest",
        "TaskHandle",
        "TaskRecord",
        "TaskRequest",
        "TaskResult",
        "TaskTable",
    ]:
        assert hasattr(multi, name)

    for name in [
        "AgentCard",
        "AgentCoordinator",
        "AgentInbox",
        "InMemoryRegistry",
        "TaskTable",
    ]:
        assert hasattr(agentos, name)

    assert not hasattr(multi, "MessageBus")
    assert not hasattr(multi, "SpawnManager")


def test_no_public_snake_case_package_alias() -> None:
    legacy_snake_name = "agent" + "_os"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_snake_name)


def test_remote_registry_and_channel_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    registry = importlib.import_module("agentos.registry")
    channels = importlib.import_module("agentos.channels")

    for name in [
        "AgentResolver",
        "InMemoryAgentRegistryStore",
        "JsonFileAgentRegistryStore",
        "PersistentAgentRegistry",
        "PostgresAgentRegistryStore",
        "ServiceResolver",
        "StaticResolver",
    ]:
        assert hasattr(registry, name)

    for name in [
        "A2AAdapter",
        "A2AServerAdapter",
        "A2ATransport",
        "AgentA2ATaskRunner",
        "AgentHealth",
        "AgentSessionProvider",
        "AllowAllChannelAuthPolicy",
        "AsgiAgentApp",
        "ChannelAuthPolicy",
        "ChannelError",
        "ChannelTurnRequest",
        "ChannelTurnResult",
        "HttpAgentChannel",
        "InMemoryAgentSessionProvider",
        "SseAgentChannel",
    ]:
        assert hasattr(channels, name)

    for name in [
        "A2AAdapter",
        "A2AServerAdapter",
        "AgentResolver",
        "AsgiAgentApp",
        "HttpAgentChannel",
        "InMemoryAgentSessionProvider",
        "PersistentAgentRegistry",
        "PostgresAgentRegistryStore",
        "RemoteTaskExecutor",
        "ServiceResolver",
        "SseAgentChannel",
        "StaticResolver",
    ]:
        assert hasattr(agentos, name)


def test_runtime_context_messages_do_not_import_channels() -> None:
    for package in ["runtime", "context", "messages"]:
        for path in (PROJECT_ROOT / "src" / "agentos" / package).glob("*.py"):
            assert "agentos.channels" not in path.read_text(encoding="utf-8")


def test_production_sql_migrations_include_down_paths() -> None:
    migration_dir = PROJECT_ROOT / "docs" / "migrations"

    for name in [
        "2026-05-07-postgres-agent-registry.sql",
        "2026-05-07-postgres-memory-backends.sql",
        "2026-05-07-sqlite-session-persistence.sql",
    ]:
        text = (migration_dir / name).read_text(encoding="utf-8")

        assert "-- migrate:up" in text
        assert "-- migrate:down" in text


def test_qdrant_recall_collection_migration_script_exists() -> None:
    text = (
        PROJECT_ROOT
        / "docs"
        / "migrations"
        / "2026-05-07-qdrant-recall-collection.py"
    ).read_text(encoding="utf-8")

    assert "create_collection" in text
    assert "agentos_recall" in text
    assert "AGENTOS_QDRANT_VECTOR_SIZE" in text
    assert "session_id" in text
