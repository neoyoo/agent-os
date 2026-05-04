import importlib

import pytest


def test_public_package_imports_as_agentos_pep8_name() -> None:
    package = importlib.import_module("agentos")

    assert package.__version__ == "0.1.0"


def test_legacy_mixed_case_package_name_is_not_public_api() -> None:
    legacy_name = "agent" + "Os"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_name)


def test_public_api_uses_responsibility_specific_names() -> None:
    runtime = importlib.import_module("agentos.runtime")
    capabilities = importlib.import_module("agentos.capabilities")
    hooks = importlib.import_module("agentos.hooks")
    providers = importlib.import_module("agentos.providers")

    assert hasattr(runtime, "QueryLoop")
    assert hasattr(runtime, "ProviderRequestBuilder")
    assert not hasattr(runtime, "AgentLoop")
    assert not hasattr(runtime, "RequestBuilder")
    assert not hasattr(runtime, "RuntimeEvent")

    assert hasattr(capabilities, "ToolCallRouter")
    assert not hasattr(capabilities, "CapabilityRuntime")

    assert hasattr(hooks, "HookManager")
    assert not hasattr(hooks, "HookRuntime")

    assert hasattr(providers, "Provider")
    assert not hasattr(providers, "ProviderRuntime")


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
    }


def test_phase5_phase6_public_api_exports() -> None:
    capabilities = importlib.import_module("agentos.capabilities")
    events = importlib.import_module("agentos.events")
    persistence = importlib.import_module("agentos.persistence")
    observability = importlib.import_module("agentos.observability")

    for name in [
        "SkillDefinition",
        "SkillRegistry",
        "SkillLoadResult",
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
        "SQLitePersistence",
        "SnapshotLoadError",
    ]:
        assert hasattr(persistence, name)

    for name in [
        "EventLog",
        "EventRecord",
        "ObservabilityContext",
        "RuntimeTraceContext",
        "TraceIds",
        "TraceRecord",
        "TraceContextPropagator",
        "EventTraceProjector",
        "OTelAdapter",
        "LangfuseAdapter",
        "current_observability_context",
        "current_runtime_trace_context",
        "current_trace_ids",
        "inject_trace_headers",
        "use_default_trace_propagator",
        "use_observability_context",
        "use_runtime_trace_context",
    ]:
        assert hasattr(observability, name)

    assert observability.EventSubscriber is events.EventSubscriber


def test_no_public_snake_case_package_alias() -> None:
    legacy_snake_name = "agent" + "_os"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_snake_name)
