import ast
import importlib
import inspect
import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_API_INVENTORY = PROJECT_ROOT / "docs" / "public-api-inventory.json"
ONLINE_README = PROJECT_ROOT / "docs" / "readme-online.md"
REMOVED_ASYNC_LOOP_NAME = "Async" + "QueryLoop"


def _load_public_api_inventory() -> dict[str, object]:
    return json.loads(PUBLIC_API_INVENTORY.read_text(encoding="utf-8"))


def _normalize_signature_text(signature: str) -> str:
    return signature.replace("pathlib._local.Path", "pathlib.Path").replace(
        "frozenset({'task', 'session'})",
        "frozenset({'session', 'task'})",
    )


def test_public_api_signature_helper_normalizes_pathlib_local_path() -> None:
    signature = "(path: pathlib._local.Path) -> pathlib._local.Path"

    assert _normalize_signature_text(signature) == (
        "(path: pathlib.Path) -> pathlib.Path"
    )


def _public_export_names(module: object) -> set[str]:
    exported_names = getattr(module, "__all__", None)
    assert exported_names is not None, (
        f"{module.__name__} must define __all__ for public API inventory"
    )
    return {str(name) for name in exported_names}


def test_public_package_imports_as_agentos_pep8_name() -> None:
    package = importlib.import_module("agentos")

    assert package.__version__ == "0.2.0a1"


def test_public_api_inventory_is_machine_readable_and_current() -> None:
    inventory = _load_public_api_inventory()

    assert inventory["schema"] == "agentos.public_api_inventory"
    assert inventory["schema_version"] == 1
    assert {"branch", "commit"}.isdisjoint(inventory)
    assert inventory["package"] == "agentos"
    assert inventory["generated_by"] == "scripts/generate_public_api_inventory.py"
    assert inventory["signature_format"] == (
        "normalized inspect.signature string or non-callable"
    )
    modules = inventory["modules"]
    assert isinstance(modules, dict)

    required_modules = {
        "agentos",
        "agentos.channels",
        "agentos.multi",
        "agentos.runtime",
        "agentos.workspace",
        "agentos.registry",
        "agentos.deployment",
        "agentos.readiness",
        "agentos.release",
        "agentos.sync",
    }
    assert required_modules <= set(modules)

    agentos_exports = modules["agentos"]["exports"]
    assert agentos_exports["AgentBuilder"]["stability"] == "stable"
    assert agentos_exports["AsgiAgentApp"]["stability"] == "stable"
    assert agentos_exports["CompareAndSavePlanStore"]["stability"] == "stable"
    assert agentos_exports["PlanStoreRecord"]["stability"] == "stable"
    assert agentos_exports["PlanConflictError"]["stability"] == "stable"
    assert modules["agentos.channels"]["exports"]["A2AOperationServer"][
        "stability"
    ] == "experimental"
    assert modules["agentos.registry"]["exports"]["NacosAgentRegistryAdapter"][
        "stability"
    ] == "experimental"
    state_plane = importlib.import_module("agentos.state_plane")
    assert hasattr(state_plane, "ReferenceStatePlaneStack")

    for module_name, module_payload in modules.items():
        module = importlib.import_module(module_name)
        exports = module_payload["exports"]
        assert isinstance(exports, dict)
        assert set(exports) == _public_export_names(module)
        for export_name, export_payload in exports.items():
            assert hasattr(module, export_name), f"{module_name}.{export_name}"
            assert export_payload["stability"] in {"stable", "experimental"}
            exported = getattr(module, export_name)
            if callable(exported):
                try:
                    signature = str(inspect.signature(exported))
                except (TypeError, ValueError):
                    signature = "unavailable"
                assert _normalize_signature_text(export_payload["signature"]) == (
                    _normalize_signature_text(signature)
                )
            else:
                assert export_payload["signature"] == "non-callable"


def test_root_public_api_inventory_only_contains_stable_exports() -> None:
    inventory = _load_public_api_inventory()
    root_exports = inventory["modules"]["agentos"]["exports"]

    experimental_root_exports = sorted(
        name
        for name, payload in root_exports.items()
        if payload["stability"] == "experimental"
    )

    assert experimental_root_exports == []


def test_root_namespace_does_not_expose_experimental_aliases() -> None:
    agentos = importlib.import_module("agentos")
    inventory = _load_public_api_inventory()
    root_exports = inventory["modules"]["agentos"]["exports"]

    assert set(agentos.__all__) == set(root_exports)
    for name in [
        "A2AAdapter",
        "A2AOperationServer",
        "AgentCard",
        "BackendVerificationRecord",
        "NacosAgentRegistryAdapter",
        "PlannerRuntime",
        "ReferenceLiveBackendProbePack",
        "ReferenceStatePlaneStack",
        "TeamRuntime",
        "WorkerProcessSupervisor",
    ]:
        assert not hasattr(agentos, name), name


def test_documented_stable_namespaces_are_governed() -> None:
    api_stability = (PROJECT_ROOT / "docs" / "api-stability.md").read_text(
        encoding="utf-8",
    )
    inventory = _load_public_api_inventory()

    documented_namespaces = set(
        re.findall(r"- `(agentos(?:\.[a-z_]+)*)`", api_stability),
    )

    assert documented_namespaces == set(inventory["modules"])


def test_public_api_inventory_records_protocol_method_contracts() -> None:
    inventory = _load_public_api_inventory()
    modules = inventory["modules"]
    assert isinstance(modules, dict)

    for module_name, module_payload in modules.items():
        module = importlib.import_module(module_name)
        exports = module_payload["exports"]
        assert isinstance(exports, dict)
        for export_name, export_payload in exports.items():
            exported = getattr(module, export_name)
            if not getattr(exported, "_is_protocol", False):
                continue
            expected_methods = {
                method_name: str(inspect.signature(method))
                for method_name, method in inspect.getmembers(
                    exported,
                    predicate=inspect.isfunction,
                )
                if not method_name.startswith("_")
            }
            assert expected_methods, f"{module_name}.{export_name}"
            assert export_payload["methods"] == expected_methods


def test_public_testing_contract_helpers_do_not_use_optimized_asserts() -> None:
    contract_dir = PROJECT_ROOT / "src" / "agentos" / "testing" / "contracts"

    offenders: list[str] = []
    for path in sorted(contract_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert offenders == []


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
    for name in [
        "RuntimeProfile",
        "RuntimeCompositionProfile",
        "ChannelRuntimeProfile",
        "DistributedRuntimeProfile",
        "DistributedWebSessionOperationsProfile",
        "DistributedWebRuntimeProfile",
        "DistributedTeamRuntimeProfile",
        "ProductionStatePlaneDeploymentProfile",
        "WorkerProcessLifecycleDeploymentProfile",
        "LocalRuntimeProfile",
        "WebRuntimeProfile",
        "DistributedAgentProfile",
    ]:
        assert hasattr(runtime, name)
        if name in agentos.__all__:
            assert hasattr(agentos, name)
        else:
            assert not hasattr(agentos, name)
    assert not hasattr(runtime, "AgentLoop")
    assert not hasattr(runtime, "RequestBuilder")
    assert not hasattr(runtime, "RuntimeEvent")

    assert hasattr(capabilities, "ToolCallRouter")
    for name in [
        "ToolPathSandboxRule",
        "ToolSandboxError",
        "ToolSandboxPolicy",
        "WorkspaceToolSandboxPolicy",
    ]:
        assert hasattr(capabilities, name)
        assert not hasattr(agentos, name)
    assert not hasattr(capabilities, "CapabilityRuntime")

    assert hasattr(hooks, "HookManager")
    assert not hasattr(hooks, "HookRuntime")

    assert hasattr(providers, "Provider")
    assert not hasattr(providers, "ProviderRuntime")

    assert hasattr(agentos, "QueryLoop")
    assert not hasattr(agentos, REMOVED_ASYNC_LOOP_NAME)
    assert hasattr(agentos, "ProviderRequestBuilder")
    assert not hasattr(agentos, "Provider")
    assert not hasattr(agentos, "ToolCallRouter")
    assert not hasattr(agentos, "HookManager")


def test_phase2_legacy_message_boundaries_are_removed() -> None:
    attachments = importlib.import_module("agentos.attachments")
    messages = importlib.import_module("agentos.messages")
    providers = importlib.import_module("agentos.providers")

    assert not hasattr(messages, "Message")
    for name in [
        "ProviderMessage",
        "UserMessage",
        "AssistantMessage",
        "ToolResultMessage",
        "ProviderMessageContent",
        "provider_message_to_dict",
        "provider_message_from_dict",
    ]:
        assert not hasattr(providers, name), name
    assert importlib.util.find_spec("agentos.messages._migration") is None
    assert importlib.util.find_spec("agentos.providers.messages") is None
    assert not hasattr(attachments.AttachmentRuntime, "project_provider_messages")

    online_readme = ONLINE_README.read_text(encoding="utf-8")
    for legacy_reference in [
        "ProviderMessage",
        "UserMessage",
        "AssistantMessage",
        "ToolResultMessage",
        "ProviderMessageContent",
        "provider_message_to_dict",
        "provider_message_from_dict",
        "project_provider_messages",
        "materialize_provider_messages",
        "providers/messages.py",
    ]:
        assert legacy_reference not in online_readme, legacy_reference


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
        "load_attachment",
    }


def test_phase5_phase6_public_api_exports() -> None:
    capabilities = importlib.import_module("agentos.capabilities")
    events = importlib.import_module("agentos.events")
    persistence = importlib.import_module("agentos.persistence")
    observability = importlib.import_module("agentos.observability")

    for name in [
        "BuiltinSkillSource",
        "ChainedSkillSource",
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

    assert hasattr(capabilities, "register_skill_loader_tools")
    assert not hasattr(capabilities, "register_skill_loader_tool")

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


def test_memory_recall_and_session_storage_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    memory = importlib.import_module("agentos.memory")
    persistence = importlib.import_module("agentos.persistence")
    recall = importlib.import_module("agentos.recall")

    for name in [
        "BoundMemoryProjectionProvider",
        "EpisodicCategory",
        "InMemoryMemoryStore",
        "MemoryAccessPolicy",
        "MemoryCandidate",
        "MemoryCategory",
        "MemoryKind",
        "MemoryRecord",
        "MemoryRuntime",
        "MemorySelectionContext",
        "MemoryStore",
        "SemanticCategory",
    ]:
        assert hasattr(memory, name)

    for name in [
        "CompressedSegmentPackage",
        "InMemoryRecallIndex",
        "QdrantRecallIndex",
        "RecallCandidate",
        "RecallIndex",
        "SegmentRecallDocument",
        "SegmentRepository",
        "TextEmbeddingProvider",
    ]:
        assert hasattr(recall, name)

    for name in [
        "DurableSessionStore",
        "HotSessionState",
        "HotSessionStore",
        "InMemoryDurableSessionStore",
        "InMemoryHotSessionStore",
        "PostgresDurableSessionStore",
        "RedisHotSessionStore",
    ]:
        assert hasattr(persistence, name)

    for name in [
        "CompressedSegmentPackage",
        "DurableSessionStore",
        "HotSessionState",
        "HotSessionStore",
        "QdrantRecallIndex",
        "RecallCandidate",
        "RecallIndex",
        "RedisHotSessionStore",
        "SegmentRecallDocument",
        "TextEmbeddingProvider",
    ]:
        assert not hasattr(memory, name)

    for name in [
        "CompressedSegmentPackage",
        "MemoryRecord",
        "MemoryRuntime",
        "MemorySelectionContext",
        "MemoryStore",
        "PostgresDurableSessionStore",
        "QdrantRecallIndex",
        "RedisHotSessionStore",
        "SegmentRecallDocument",
    ]:
        assert not hasattr(agentos, name)


def test_phase8_multi_agent_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    multi = importlib.import_module("agentos.multi")

    for name in [
        "AgentCard",
        "AgentCoordinator",
        "AgentCoordinatorPlanStepDispatcher",
        "AgentCoordinationTools",
        "AgentEnvelope",
        "AgentInbox",
        "AgentInboxFullError",
        "AgentInboxMissingError",
        "AgentTaskNoticeStore",
        "AllowAllPlannerToolAuthorizationPolicy",
        "AllowAllTeamToolAuthorizationPolicy",
        "CompareAndSavePlanStore",
        "ClaimGuardedPlanStore",
        "ContinuationErrorRecord",
        "ContinuationTrigger",
        "DefaultPlannerToolAuthorizationPolicy",
        "DefaultTeamToolAuthorizationPolicy",
        "EvidenceHandle",
        "ExpertAgentRunner",
        "InMemoryPlanClaimStore",
        "InMemoryRegistry",
        "InMemoryPlanStore",
        "InMemoryTeamStore",
        "InMemoryTeamUiStreamStore",
        "InMemoryTeamWorkerCancellationStore",
        "InMemoryTeamWorkerSessionProvider",
        "InMemoryTeamWorkerRetryStore",
        "LocalContinuationTrigger",
        "LocalTeamWakeupTrigger",
        "SpawnExecutor",
        "SubagentInitRequest",
        "PlanDecomposition",
        "PlanConflictError",
        "PlanDecompositionGatePolicy",
        "PlanDecompositionGateReport",
        "PlanDecompositionValidationReport",
        "PlanAssignment",
        "PlanClaimRecord",
        "PlanClaimResult",
        "PlanClaimStatus",
        "PlanClaimSweepReport",
        "PlanClaimSweepSkip",
        "PlanClaimSweepStore",
        "PlanClaimStore",
        "PlanClaimedSchedulerTickReport",
        "PlanClaimedSchedulerTickSkip",
        "PlanDispatchReport",
        "PlanDispatchSkip",
        "PlanError",
        "PlanRetryPolicy",
        "PlanSchedulerRetryReset",
        "PlanSchedulerTickReport",
        "PlanState",
        "PlanStep",
        "PlanStepSpec",
        "PlanStore",
        "PlanStoreRecord",
        "PlannerSchedulablePlan",
        "PlannerSchedulablePlanReason",
        "PlannerDecompositionPolicyDeploymentProfile",
        "PlannerLlmDecompositionGovernanceProfile",
        "PlannerLlmGovernanceEvidenceRecord",
        "PlannerLlmGovernanceEvidenceGateReport",
        "PlannerOrchestrationDeploymentProfile",
        "PlannerSchedulerGovernanceDeploymentProfile",
        "PlannerClaimedSchedulerDaemon",
        "PlannerClaimedSchedulerDaemonError",
        "PlannerClaimedSchedulerDaemonState",
        "PlannerClaimedSchedulerDaemonStatus",
        "PlannerStaleClaimSweepProfile",
        "PlannerWorkerDispatchSupervisionProfile",
        "PlannerSchedulerDaemon",
        "PlannerSchedulerDaemonError",
        "PlannerSchedulerDaemonState",
        "PlannerSchedulerDaemonStatus",
        "PlannerToolAuthorizationError",
        "PlannerToolAuthorizationPolicy",
        "PlannerTools",
        "PlannerRuntime",
        "plan_to_working_state_summary",
        "PostgresPlanStore",
        "PostgresPlanClaimStore",
        "PostgresTeamStore",
        "PostgresTeamUiStreamStore",
        "PostgresTeamWorkerCancellationStore",
        "PostgresTeamWorkerRetryStore",
        "TaskHandle",
        "TaskRecord",
        "TaskRequest",
        "TaskResult",
        "TaskTable",
        "SubAgentTemplate",
        "TeamError",
        "TeamMemberRecord",
        "TeamMessage",
        "TeamNoticeProvider",
        "TeamNoticeStore",
        "TeamRecord",
        "TeamRuntime",
        "TeamStore",
        "TeamToolAuthorizationError",
        "TeamToolAuthorizationPolicy",
        "TeamTools",
        "TeamWakeupTrigger",
        "TeamUiEvent",
        "TeamUiEventKind",
        "TeamUiStreamStore",
        "TeamWorkerAgentProvider",
        "TeamWorkerCancellationRecord",
        "TeamWorkerCancellationStatus",
        "TeamWorkerCancellationStore",
        "TeamWorkerDaemon",
        "TeamWorkerDaemonState",
        "TeamWorkerDaemonStatus",
        "TeamWorkerPermissionError",
        "TeamWorkerPermissionPolicy",
        "TeamWorkerRetryPolicy",
        "TeamWorkerRetryRecord",
        "TeamWorkerRetryStatus",
        "TeamWorkerRetryStore",
        "TeamWorkerRunError",
        "TeamWorkerRunResult",
        "TeamWorkerRunner",
        "TeamWorkerSession",
        "TeamWorkerSessionProvider",
        "TeamWorkerSessionRequest",
    ]:
        assert hasattr(multi, name)

    for name in [
        "CompareAndSavePlanStore",
        "InMemoryPlanStore",
        "PlanConflictError",
        "PlanStoreRecord",
        "PostgresPlanStore",
    ]:
        assert hasattr(agentos, name)
    for name in [
        "AgentCard",
        "AgentCoordinator",
        "PlannerRuntime",
        "TeamRuntime",
        "PostgresPlanClaimStore",
        "PostgresTeamStore",
    ]:
        assert not hasattr(agentos, name)

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
        "NacosAgentCardResolver",
        "NacosAgentRegistryAdapter",
        "NacosRegistryClient",
        "NacosRegistryConfig",
        "NacosRegistryError",
        "NacosRegistryEvidence",
        "PersistentAgentRegistry",
        "PostgresAgentRegistryStore",
        "ServiceResolver",
        "StaticResolver",
        "agent_card_to_nacos_metadata",
        "nacos_instance_to_agent_card",
    ]:
        assert hasattr(registry, name)

    for name in [
        "A2AAdapter",
        "A2AAgentCapabilities",
        "A2AAgentCard",
        "A2AAgentExtension",
        "A2AAgentInterface",
        "A2AAgentProvider",
        "A2AAgentSkill",
        "A2AAuthProvider",
        "A2ABearerCredential",
        "A2ACardSignature",
        "A2ACardSigner",
        "A2ACardTrustError",
        "A2ACardTrustStore",
        "A2ACardVerifier",
        "A2AConformanceCheck",
        "A2AConformanceFinding",
        "A2AConformanceHarness",
        "A2AConformanceReport",
        "A2AExternalConformanceExecutionProfile",
        "A2AExternalConformanceExecutionRecord",
        "A2AExternalConformanceGateReport",
        "A2AExternalConformanceRunner",
        "A2AExternalConformanceCliRunner",
        "A2AExternalConformanceInvocationGateReport",
        "A2AExternalConformanceInvocationPlan",
        "A2AExternalConformanceImportError",
        "A2AExternalConformanceReportImporter",
        "A2AEgressPolicyError",
        "A2AEgressUrlPolicy",
        "A2ACredentialRotationError",
        "A2AInboundAuthError",
        "A2AInboundAuthPolicy",
        "A2AJwtClaims",
        "A2AJwtVerifier",
        "A2AOidcDiscoveryError",
        "A2AOperationInboundAuthPolicy",
        "A2AResourceInboundAuthPolicy",
        "A2ATenantRbacRule",
        "A2ARateLimitError",
        "A2AArtifact",
        "A2AExtensionNegotiationError",
        "A2AExtensionNegotiationPolicy",
        "A2AExtensionNegotiationResult",
        "A2AMessage",
        "A2AMessagePart",
        "A2AMessageStreamEvent",
        "A2AOperationClient",
        "A2AOperationError",
        "A2AOperationRateLimitPolicy",
        "A2AOperationRequest",
        "A2AOperationResponse",
        "A2AOperationRunner",
        "A2AOperationServer",
        "A2APeerIdResolver",
        "A2AProtocolVersionPolicy",
        "A2APushNotificationAuthentication",
        "A2APushNotificationConfig",
        "A2APushNotificationConfigError",
        "A2APushNotificationConfigStore",
        "A2APushNotificationDaemon",
        "A2APushNotificationDaemonError",
        "A2APushNotificationDaemonState",
        "A2APushNotificationDaemonStatus",
        "A2APushNotificationDeploymentProfile",
        "A2APushNotificationDelivery",
        "A2APushNotificationDeliveryRecord",
        "A2APushNotificationDeliveryRecordStatus",
        "A2APushNotificationDeliveryStore",
        "A2APushNotificationDeliveryWorker",
        "A2APushNotificationDispatcher",
        "A2APushNotificationHealthPolicy",
        "A2APushNotificationHealthReport",
        "A2APushNotificationHealthStatus",
        "A2APushNotificationRetryPolicy",
        "A2APushNotificationUrlPolicy",
        "A2AStreamLifecycleDeploymentProfile",
        "A2AServerAdapter",
        "A2ATransport",
        "A2ACardResolver",
        "A2ATask",
        "A2ATaskArtifactUpdateEvent",
        "A2ATaskLifecycleRunner",
        "A2ATaskSubscriptionEvent",
        "AgentA2AOperationRunner",
        "AgentA2ATaskRunner",
        "AgentHealth",
        "HmacA2ACardSigner",
        "HmacA2ACardVerifier",
        "JwksA2ACardTrustStore",
        "HostAllowListA2AEgressUrlPolicy",
        "AsyncAgentSessionProvider",
        "AgentSessionProvider",
        "AllowAllA2AInboundAuthPolicy",
        "AllowAllChannelAuthPolicy",
        "AllowAllTeamUiAuthPolicy",
        "AsgiAgentApp",
        "ChannelAuthError",
        "ChannelAuthPolicy",
        "ChannelError",
        "ChannelTurnRequest",
        "ChannelTurnResult",
        "ClaimsTenantRbacA2AInboundAuthPolicy",
        "DurableAgentSessionProvider",
        "HttpAgentChannel",
        "HostAllowListA2APushNotificationUrlPolicy",
        "HmacA2AJwtVerifier",
        "InMemoryA2APushNotificationConfigStore",
        "InMemoryA2APushNotificationDeliveryStore",
        "InMemoryAgentSessionProvider",
        "InMemorySessionLeaseStore",
        "InMemorySseEventBuffer",
        "InMemorySseTurnControlStore",
        "JwksA2AJwtVerifier",
        "OperationAllowListA2AInboundAuthPolicy",
        "OidcDiscoveryMetadata",
        "OidcDiscoveryMetadataProvider",
        "OidcClaimsA2AInboundAuthPolicy",
        "PeerAllowListA2AInboundAuthPolicy",
        "PeerKeyA2AOperationRateLimitPolicy",
        "PostgresA2APushNotificationConfigStore",
        "PostgresA2APushNotificationDeliveryStore",
        "PublicHttpsA2AEgressUrlPolicy",
        "PublicHttpsA2APushNotificationUrlPolicy",
        "RejectAllA2AInboundAuthPolicy",
        "RejectAllChannelAuthPolicy",
        "RejectAllTeamUiAuthPolicy",
        "RedisSessionLeaseStore",
        "LeaseFencedSessionPersistence",
        "RedisSseEventBuffer",
        "RedisSseTurnControlStore",
        "ResourceAllowListA2AInboundAuthPolicy",
        "RotatingA2ACardTrustKey",
        "RotatingA2ACardTrustStore",
        "RotatingBearerA2AAuthProvider",
        "RotatingBearerA2ACredentialStore",
        "RotatingBearerA2AInboundAuthPolicy",
        "RotatingHmacA2ACardSigner",
        "SessionLease",
        "SessionLeaseError",
        "SessionLeaseStore",
        "SnapshotAgentFactory",
        "SseAgentChannel",
        "SseEventBuffer",
        "SseReplayWindow",
        "SseTurnAlreadyActiveError",
        "SseTurnControlState",
        "SseTurnControlStore",
        "StaticA2ACardTrustStore",
        "StaticBearerA2AAuthProvider",
        "StaticBearerA2AInboundAuthPolicy",
        "a2a_artifact_from_dict",
        "a2a_artifact_to_dict",
        "a2a_message_stream_event_from_dict",
        "a2a_card_from_agent_card",
        "a2a_card_from_dict",
        "a2a_card_to_dict",
        "a2a_push_notification_config_from_dict",
        "a2a_push_notification_config_to_dict",
        "a2a_push_notification_payload_to_dict",
        "a2a_state_from_task_status",
        "a2a_task_artifact_update_event_from_dict",
        "a2a_task_artifact_update_event_to_dict",
        "a2a_task_from_task_record",
        "a2a_task_subscription_event_from_dict",
        "a2a_task_subscription_event_to_dict",
        "parse_a2a_sse_events",
    ]:
        assert hasattr(channels, name)

    for name in [
        "AsgiAgentApp",
        "DurableAgentSessionProvider",
        "LeaseFencedSessionPersistence",
        "PersistentAgentRegistry",
        "PostgresAgentRegistryStore",
        "RedisSessionLeaseStore",
        "SessionLeaseStore",
    ]:
        assert hasattr(agentos, name)
    for name in [
        "A2AAdapter",
        "A2AOperationServer",
        "NacosAgentRegistryAdapter",
        "RemoteTaskExecutor",
        "agent_card_to_nacos_metadata",
    ]:
        assert not hasattr(agentos, name)


def test_distributed_sse_public_api_inventory_covers_shared_replay_and_control() -> None:
    inventory = _load_public_api_inventory()
    channel_exports = inventory["modules"]["agentos.channels"]["exports"]

    for name in [
        "InMemorySseEventBuffer",
        "RedisSseEventBuffer",
        "SseEventBuffer",
        "SseReplayWindow",
        "InMemorySseTurnControlStore",
        "RedisSseTurnControlStore",
        "SseTurnAlreadyActiveError",
        "SseTurnControlState",
        "SseTurnControlStore",
    ]:
        assert channel_exports[name]["stability"] == "stable"


def test_workspace_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    workspace = importlib.import_module("agentos.workspace")

    for name in [
        "LocalWorkspaceExecutionBackend",
        "LocalWorkspaceProvider",
        "SandboxBackend",
        "WorkspaceExecutionIsolationProfile",
        "WorkspaceExecutionBackend",
        "WorkspaceExecutionError",
        "WorkspaceExecutionPolicy",
        "WorkspaceExecutionRequest",
        "WorkspaceExecutionResult",
        "WorkspaceHandle",
        "WorkspacePolicy",
        "WorkspacePolicyError",
        "WorkspaceProvider",
        "WorkspaceRequest",
        "WorkspaceScope",
    ]:
        assert hasattr(workspace, name)
        if name in agentos.__all__:
            assert hasattr(agentos, name)
        else:
            assert not hasattr(agentos, name)


def test_readiness_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    readiness = importlib.import_module("agentos.readiness")

    for name in [
        "AgentFormReadiness",
        "ProductionReadinessEvidenceBundle",
        "ReadinessDimension",
        "ReadinessEvidenceCheck",
        "ReadinessEvidenceStatus",
        "ReadinessLevel",
        "REQUIRED_READINESS_DIMENSIONS",
        "get_agent_form_readiness",
        "list_agent_form_readiness",
    ]:
        assert hasattr(readiness, name)
        if name in agentos.__all__:
            assert hasattr(agentos, name)
        else:
            assert not hasattr(agentos, name)


def test_release_evidence_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    release = importlib.import_module("agentos.release")

    for name in [
        "RELEASE_EVIDENCE_REQUIRED_GATES",
        "ReleaseEvidenceGateStatus",
        "ReleaseEvidenceValidationReport",
        "validate_release_candidate_evidence_manifest",
        "validate_release_evidence_manifest",
    ]:
        assert hasattr(release, name)
        assert hasattr(agentos, name)


def test_skill_release_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    skills = importlib.import_module("agentos.skills")

    for name in [
        "SkillReleaseFile",
        "SkillReleaseManifest",
        "SkillReleaseDriftReport",
        "build_skill_release_manifest",
        "compare_skill_release_manifests",
    ]:
        assert hasattr(skills, name)
        assert not hasattr(agentos, name)


def test_distributed_session_adapter_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    channels = importlib.import_module("agentos.channels")
    persistence = importlib.import_module("agentos.persistence")

    assert hasattr(channels, "RedisSessionLeaseStore")
    assert hasattr(channels, "LeaseFencedSessionPersistence")
    assert hasattr(persistence, "PostgresSessionSnapshotPersistence")
    assert hasattr(agentos, "RedisSessionLeaseStore")
    assert hasattr(agentos, "LeaseFencedSessionPersistence")
    assert not hasattr(agentos, "PostgresSessionSnapshotPersistence")


def test_distributed_session_public_api_exposes_snapshot_fencing_fields() -> None:
    inventory = _load_public_api_inventory()

    channel_exports = inventory["modules"]["agentos.channels"]["exports"]
    persistence_exports = inventory["modules"]["agentos.persistence"]["exports"]

    assert "fence: 'int' = 0" in channel_exports["SessionLease"]["signature"]
    assert (
        "lease_fence: int = 0"
        in persistence_exports["SessionSnapshotRecord"]["signature"]
    )


def test_distributed_web_runtime_profile_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    runtime = importlib.import_module("agentos.runtime")

    assert hasattr(runtime, "DistributedWebRuntimeProfile")
    assert hasattr(agentos, "DistributedWebRuntimeProfile")
    assert hasattr(runtime, "DistributedWebSessionOperationsProfile")
    assert hasattr(agentos, "DistributedWebSessionOperationsProfile")
    assert hasattr(runtime, "DistributedTeamRuntimeProfile")
    assert not hasattr(agentos, "DistributedTeamRuntimeProfile")
    assert hasattr(runtime, "ProductionStatePlaneDeploymentProfile")
    assert not hasattr(agentos, "ProductionStatePlaneDeploymentProfile")
    assert hasattr(runtime, "WorkerProcessLifecycleDeploymentProfile")
    assert not hasattr(agentos, "WorkerProcessLifecycleDeploymentProfile")


def test_runtime_composition_profiles_do_not_advertise_agent_builder_contract() -> None:
    agentos = importlib.import_module("agentos")
    runtime = importlib.import_module("agentos.runtime")
    inventory = _load_public_api_inventory()
    runtime_exports = inventory["modules"]["agentos.runtime"]["exports"]

    assert hasattr(runtime, "RuntimeCompositionProfile")
    assert not hasattr(agentos, "RuntimeCompositionProfile")
    assert "build_agent" not in runtime_exports["RuntimeCompositionProfile"][
        "methods"
    ]
    for name in ["DistributedAgentProfile", "DistributedTeamRuntimeProfile"]:
        assert "methods" not in runtime_exports[name] or (
            "build_agent" not in runtime_exports[name]["methods"]
        )


def test_agent_service_reference_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    service = importlib.import_module("agentos.service")

    for name in [
        "AGENT_SERVICE_REFERENCE_REQUIRED_COMPONENTS",
        "AgentServiceReference",
        "AgentServiceReferenceProfile",
    ]:
        assert hasattr(service, name)
        assert not hasattr(agentos, name)


def test_reference_state_plane_stack_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    state_plane = importlib.import_module("agentos.state_plane")

    for name in [
        "REFERENCE_STATE_PLANE_REQUIRED_COMPONENTS",
        "ReferenceStatePlaneStack",
        "ReferenceStatePlaneStackProfile",
    ]:
        assert hasattr(state_plane, name)
        assert not hasattr(agentos, name)


def test_reference_live_backend_probe_pack_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    probes = importlib.import_module("agentos.probes")

    for name in [
        "REFERENCE_LIVE_BACKEND_PROBE_PACK_NAME",
        "ReferenceLiveBackendProbePack",
        "ReferenceLiveBackendProbeSpec",
    ]:
        assert hasattr(probes, name)
        assert not hasattr(agentos, name)


def test_deployment_worker_process_supervisor_public_api_exports() -> None:
    agentos = importlib.import_module("agentos")
    deployment = importlib.import_module("agentos.deployment")

    for name in [
        "BackendVerificationCliRunner",
        "BackendVerificationInvocationPlan",
        "BackendVerificationReportImportError",
        "BackendVerificationReportImporter",
        "BackendVerificationRecord",
        "BackendVerificationRunner",
        "BackendVerificationStatus",
        "DeploymentLiveBackendVerificationGateReport",
        "DeploymentLiveBackendVerificationProfile",
        "DeploymentLiveBackendVerificationRunResult",
        "LIVE_BACKEND_VERIFICATION_EXPECTED_BACKEND_KINDS",
        "LIVE_BACKEND_VERIFICATION_STATE_PLANE_BACKENDS",
        "WorkerProcessSpec",
        "WorkerProcessState",
        "WorkerProcessSupervisor",
        "LocalSubprocessWorkerSupervisor",
    ]:
        assert hasattr(deployment, name)
        assert not hasattr(agentos, name)


def test_runtime_context_messages_do_not_import_channels() -> None:
    for package in ["runtime", "context", "messages"]:
        for path in (PROJECT_ROOT / "src" / "agentos" / package).glob("*.py"):
            if path.name == "profile.py":
                continue
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

