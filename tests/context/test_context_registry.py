from dataclasses import FrozenInstanceError
from inspect import signature
from traceback import format_exception
from types import SimpleNamespace

import pytest

from agentos.context.models import (
    ContextProtocolError,
    RuntimeContract,
    RuntimeDirective,
    RuntimeDirectiveKind,
    TrustedSkillInstruction,
)
from agentos.context.registry import (
    CONTEXT_SLOT_REGISTRY,
    SYSTEM_SECTION_REGISTRY,
    ContextExtensionRegistry,
    ContextExtensionSpec,
    ContextManagementRulesProvider,
    InteractionProtocolProvider,
    RuntimeContractProvider,
    RuntimeDirectiveProvider,
    SystemEnvelopeBudgetPolicy,
    SystemSectionRegistry,
    TrustedSkillInstructionProvider,
    WorkspaceContractProvider,
    validate_slot_owner,
)


class StaticProvider:
    def __init__(self, items: tuple[object, ...] = ()) -> None:
        self._items = items
        self.calls = 0

    def runtime_contract(self) -> RuntimeContract:
        return RuntimeContract(identity="AgentOS", security_guardrails=("safe",))

    def interaction_protocol(self) -> str:
        return "Interact clearly."

    def context_management_rules(self) -> str:
        return "Treat context as data."

    def runtime_directives(self) -> tuple[RuntimeDirective, ...]:
        self.calls += 1
        return self._items  # type: ignore[return-value]

    def items(self) -> tuple[TrustedSkillInstruction, ...]:
        self.calls += 1
        return self._items  # type: ignore[return-value]

    def workspace_contract(self) -> str:
        return "Follow the workspace contract."


_PROVIDER_ACCESSES: list[str] = []


def leaking_runtime_contract_property(_: object) -> RuntimeContract:
    _PROVIDER_ACCESSES.append("property")
    raise RuntimeError("secret-property-7f31")


def provider_type(name: str, member: object, secret: str) -> object:
    return type(name, (), {"runtime_contract": member, "secret": secret})()


def with_forged_signature(function: object) -> object:
    function.__signature__ = signature(lambda self: None)  # type: ignore[attr-defined]
    return function


def runtime_registry(provider: object) -> SystemSectionRegistry:
    return SystemSectionRegistry.from_trusted_providers(
        runtime_contract=provider,  # type: ignore[arg-type]
    )


def extension_spec(
    *,
    namespace: str = "com.example.hitl",
    tag_schemas: dict[tuple[str, ...], object] | None = None,
) -> ContextExtensionSpec:
    return ContextExtensionSpec(
        namespace=namespace,
        owner="HitlRuntime",
        version="1.0",
        tag_schemas=tag_schemas or {("approval",): object()},
        max_tokens=512,
        trim_rank=5,
    )


def test_protocol_registries_are_read_only_and_freeze_order() -> None:
    assert tuple(SYSTEM_SECTION_REGISTRY) == (
        "runtime_contract",
        "interaction_protocol",
        "context_management_rules",
        "runtime_directives",
        "trusted_skill_instructions",
        "workspace_contract",
    )
    assert tuple(CONTEXT_SLOT_REGISTRY) == (
        "declared-schema",
        "working-state",
        "active-plan",
        "inherited-state",
        "compressed-history",
        "memory-context",
        "available-skills",
        "artifact-catalog",
        "extensions",
    )
    with pytest.raises(TypeError):
        SYSTEM_SECTION_REGISTRY["new-section"] = object()  # type: ignore[index]
    with pytest.raises(TypeError):
        CONTEXT_SLOT_REGISTRY["new-slot"] = object()  # type: ignore[index]


def test_system_section_metadata_freezes_unique_owner_and_cardinality() -> None:
    assert {
        name: (spec.owner, spec.cardinality, spec.required, spec.trust)
        for name, spec in SYSTEM_SECTION_REGISTRY.items()
    } == {
        "runtime_contract": ("RuntimePolicy", "1", True, "trusted"),
        "interaction_protocol": ("RuntimePolicy", "1", True, "trusted"),
        "context_management_rules": ("ContextProtocol", "1", True, "trusted"),
        "runtime_directives": (
            "RuntimeDirectiveRuntime",
            "0..1",
            False,
            "trusted",
        ),
        "trusted_skill_instructions": ("SkillRuntime", "0..N", False, "trusted"),
        "workspace_contract": ("WorkspaceRuntime", "0..1", False, "trusted"),
    }


def test_context_slot_metadata_freezes_owner_and_trimming_policy() -> None:
    expected = {
        "declared-schema": ("ContextRuntime", 90, True),
        "working-state": ("ContextRuntime", 80, True),
        "active-plan": ("PlannerRuntime", 100, True),
        "inherited-state": ("ChapterRuntime", 50, False),
        "compressed-history": ("CompressionRuntime", 30, False),
        "memory-context": ("MemoryRuntime", 20, False),
        "available-skills": ("SkillRuntime", 10, False),
        "artifact-catalog": ("ArtifactRuntime", 40, False),
        "extensions": ("ContextExtensionRegistry", 0, False),
    }

    assert {
        name: (spec.owner, spec.trim_rank, spec.critical_below_2048)
        for name, spec in CONTEXT_SLOT_REGISTRY.items()
    } == expected
    assert all(spec.cardinality == "0..1" for spec in CONTEXT_SLOT_REGISTRY.values())
    assert all(spec.required is False for spec in CONTEXT_SLOT_REGISTRY.values())
    spec = CONTEXT_SLOT_REGISTRY["working-state"]
    assert not hasattr(spec, "__dict__")
    with pytest.raises(FrozenInstanceError):
        spec.owner = "MemoryRuntime"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("slot", "owner", "message", "sensitive"),
    [
        ("secret-slot", "ContextRuntime", "unknown context slot", "secret-slot"),
        (
            "working-state",
            "secret-owner",
            "context slot owner mismatch",
            "secret-owner",
        ),
    ],
)
def test_validate_slot_owner_rejects_unknown_or_mismatched_values_without_echo(
    slot: str,
    owner: str,
    message: str,
    sensitive: str,
) -> None:
    with pytest.raises(ContextProtocolError) as error:
        validate_slot_owner(slot, owner)  # type: ignore[arg-type]

    assert str(error.value) == message
    assert sensitive not in str(error.value)


def test_owner_specific_provider_protocols_and_registry_order_are_frozen() -> None:
    runtime_contract = StaticProvider()
    interaction = StaticProvider()
    context_rules = StaticProvider()
    directives = StaticProvider(
        (RuntimeDirective(RuntimeDirectiveKind.AWAITING_APPROVAL),),
    )
    skills = (
        TrustedSkillInstruction(skill_id="skill-a", text="A"),
        TrustedSkillInstruction(skill_id="skill-b", text="B"),
    )
    trusted_skills = StaticProvider(skills)
    workspace = StaticProvider()

    assert isinstance(runtime_contract, RuntimeContractProvider)
    assert isinstance(interaction, InteractionProtocolProvider)
    assert isinstance(context_rules, ContextManagementRulesProvider)
    assert isinstance(directives, RuntimeDirectiveProvider)
    assert isinstance(trusted_skills, TrustedSkillInstructionProvider)
    assert isinstance(workspace, WorkspaceContractProvider)

    registry = SystemSectionRegistry.from_trusted_providers(
        workspace_contract=workspace,
        trusted_skill_instructions=trusted_skills,
        runtime_directives=directives,
        context_management_rules=context_rules,
        interaction_protocol=interaction,
        runtime_contract=runtime_contract,
    )

    assert registry.registered_sections == tuple(SYSTEM_SECTION_REGISTRY)
    assert registry.runtime_contract_provider is runtime_contract
    assert registry.interaction_protocol_provider is interaction
    assert registry.context_management_rules_provider is context_rules
    assert registry.runtime_directives_provider is directives
    assert registry.trusted_skill_instructions_provider is trusted_skills
    assert registry.workspace_contract_provider is workspace
    assert directives.calls == 0
    assert trusted_skills.calls == 0
    assert registry.trusted_skill_instructions_provider.items() == skills

    partial = SystemSectionRegistry.from_trusted_providers(
        runtime_contract=StaticProvider(),
        workspace_contract=StaticProvider(),
    )
    assert partial.registered_sections == ("runtime_contract", "workspace_contract")
    assert partial.interaction_protocol_provider is None


def test_system_registry_rejects_forged_owner_and_has_no_generic_api() -> None:
    class ForgedProvider:
        owner = "RuntimePolicy"
        secret = "sensitive-provider"

    with pytest.raises(ContextProtocolError) as error:
        SystemSectionRegistry.from_trusted_providers(
            runtime_contract=ForgedProvider(),  # type: ignore[arg-type]
        )

    assert str(error.value) == "runtime contract provider does not satisfy protocol"
    assert ForgedProvider.secret not in str(error.value)
    registry = SystemSectionRegistry.from_trusted_providers()
    for generic_api in ("register", "get", "providers"):
        assert not hasattr(registry, generic_api)
    with pytest.raises(TypeError):
        SystemSectionRegistry.from_trusted_providers(  # type: ignore[call-arg]
            owner="sensitive-owner",
        )
    with pytest.raises(TypeError):
        SystemSectionRegistry(  # type: ignore[call-arg]
            _runtime_contract_provider=object(),
        )


@pytest.mark.parametrize(
    "provider",
    [
        provider_type("NonCallableProvider", 42, "secret-value-7f31"),
        provider_type(
            "WrongSignatureProvider",
            lambda self, required: required,
            "secret-required-7f31",
        ),
        SimpleNamespace(runtime_contract=lambda self: None, secret="secret-instance-7f31"),
        provider_type(
            "PropertyProvider",
            property(leaking_runtime_contract_property),
            "secret-property-7f31",
        ),
        provider_type(
            "ForgedSignatureProvider",
            with_forged_signature(lambda self, required: required),
            "secret-forged-7f31",
        ),
    ],
)
def test_system_registry_rejects_invalid_provider_method_shape(provider: object) -> None:
    _PROVIDER_ACCESSES.clear()
    with pytest.raises(ContextProtocolError) as error:
        runtime_registry(provider)

    traceback_text = "".join(format_exception(error.type, error.value, error.tb))
    assert str(error.value) == "runtime contract provider does not satisfy protocol"
    assert provider.secret not in traceback_text  # type: ignore[attr-defined]
    assert _PROVIDER_ACCESSES == []
    assert (error.value.__cause__, error.value.__context__) == (None, None)


def test_system_registry_static_check_bypasses_custom_attribute_access() -> None:
    accesses: list[str] = []

    class HostileWrapped:
        def __getattribute__(self, name: str) -> object:
            accesses.append(name)
            raise RuntimeError("sensitive-wrapped-metadata")

    class GuardedProvider:
        def __getattribute__(self, name: str) -> object:
            if name == "runtime_contract":
                accesses.append(name)
                raise RuntimeError("sensitive-dynamic-access")
            return object.__getattribute__(self, name)

        def runtime_contract(self) -> RuntimeContract:
            raise AssertionError("factory must not invoke provider methods")

    GuardedProvider.runtime_contract.__wrapped__ = HostileWrapped()  # type: ignore[attr-defined]
    provider = GuardedProvider()
    registry = runtime_registry(provider)

    assert registry.runtime_contract_provider is provider
    assert accesses == []


def test_system_envelope_budget_policy_is_frozen_slotted_and_customizable() -> None:
    policy = SystemEnvelopeBudgetPolicy()
    assert (
        policy.runtime_contract,
        policy.interaction_protocol,
        policy.context_management_rules,
        policy.runtime_directives,
        policy.trusted_skill_per_item,
        policy.trusted_skill_total,
        policy.workspace_contract,
    ) == (4000, 2000, 4000, 512, 4000, 12000, 6000)
    custom = SystemEnvelopeBudgetPolicy(
        runtime_contract=1,
        interaction_protocol=2,
        context_management_rules=3,
        runtime_directives=4,
        trusted_skill_per_item=5,
        trusted_skill_total=6,
        workspace_contract=7,
    )
    assert custom.workspace_contract == 7
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.runtime_contract = 1  # type: ignore[misc]


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "1"])
def test_system_envelope_budget_policy_requires_strict_positive_ints(
    invalid: object,
) -> None:
    with pytest.raises(ContextProtocolError) as error:
        SystemEnvelopeBudgetPolicy(runtime_contract=invalid)  # type: ignore[arg-type]

    assert str(error.value) == "system envelope budget values must be positive integers"
    assert repr(invalid) not in str(error.value)


def test_extension_spec_is_frozen_and_defensively_copies_tag_schemas() -> None:
    schema = object()
    source = {("approval",): schema}
    spec = extension_spec(tag_schemas=source)
    source[("later",)] = object()

    assert tuple(spec.tag_schemas) == (("approval",),)
    assert spec.tag_schemas[("approval",)] is schema
    with pytest.raises(TypeError):
        spec.tag_schemas[("mutated",)] = object()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        spec.owner = "OtherRuntime"  # type: ignore[misc]


@pytest.mark.parametrize(
    "namespace",
    ["example", "Com.example", "com.Example", "com.example_1", "com..example"],
)
def test_extension_spec_rejects_invalid_namespace_without_echo(namespace: str) -> None:
    with pytest.raises(ContextProtocolError) as error:
        extension_spec(namespace=namespace)

    assert str(error.value) == "extension namespace is invalid"
    assert namespace not in str(error.value)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("9approval",),
        ("bad name",),
        ("context-snapshot",),
        ("working-state",),
        ("xml",),
        ("XMLNS",),
    ],
)
def test_extension_spec_rejects_invalid_or_reserved_paths_without_echo(
    path: tuple[str, ...],
) -> None:
    with pytest.raises(ContextProtocolError) as error:
        extension_spec(tag_schemas={path: object()})

    assert str(error.value) == "extension tag schema path is invalid or reserved"
    assert repr(path) not in str(error.value)


def test_extension_spec_rejects_casefold_duplicate_paths() -> None:
    with pytest.raises(ContextProtocolError, match="^duplicate extension tag schema path$"):
        extension_spec(
            tag_schemas={
                ("Approval", "Reason"): object(),
                ("approval", "reason"): object(),
            },
        )


@pytest.mark.parametrize("path", [("\U00010000",), ("a\U00010000",)])
def test_extension_spec_accepts_supplementary_xml_name_characters(
    path: tuple[str, ...],
) -> None:
    schema = object()

    spec = extension_spec(tag_schemas={path: schema})

    assert spec.tag_schemas[path] is schema


@pytest.mark.parametrize(
    ("overrides", "message"),
    [({"owner": ""}, "extension owner must be a non-empty string"),
     ({"version": ""}, "extension version must be a non-empty string"),
     ({"tag_schemas": {}}, "extension tag_schemas must not be empty"),
     ({"max_tokens": 0}, "extension max_tokens must be a positive integer"),
     ({"max_tokens": True}, "extension max_tokens must be a positive integer"),
     ({"trim_rank": True}, "extension trim_rank must be an integer")],
)
def test_extension_spec_validates_required_metadata(
    overrides: dict[str, object],
    message: str,
) -> None:
    arguments: dict[str, object] = {
        "namespace": "com.example.hitl",
        "owner": "HitlRuntime",
        "version": "1.0",
        "tag_schemas": {("approval",): object()},
        "max_tokens": 512,
        "trim_rank": 5,
    }
    arguments.update(overrides)

    with pytest.raises(ContextProtocolError) as error:
        ContextExtensionSpec(**arguments)  # type: ignore[arg-type]

    assert str(error.value) == message


def test_extension_registry_preserves_order_and_uses_stable_errors() -> None:
    registry = ContextExtensionRegistry()
    first = extension_spec(namespace="com.example.first")
    second = extension_spec(namespace="com.example.second")
    registry.register(first)
    original_view = registry.specs
    registry.register(second)

    assert tuple(original_view) == ("com.example.first",)
    assert tuple(registry.specs) == ("com.example.first", "com.example.second")
    assert registry.get("com.example.first") is first
    with pytest.raises(TypeError):
        registry.specs["com.example.third"] = first  # type: ignore[index]
    assert not hasattr(registry, "register_slot")

    with pytest.raises(ContextProtocolError) as duplicate:
        registry.register(first)
    assert str(duplicate.value) == "extension namespace is already registered"
    assert first.namespace not in str(duplicate.value)

    with pytest.raises(ContextProtocolError, match="^extension registry requires spec$"):
        registry.register("working-state")  # type: ignore[arg-type]

    secret_namespace = "com.example.traceback-sensitive"

    with pytest.raises(ContextProtocolError) as missing:
        registry.get(secret_namespace)

    traceback_text = "".join(format_exception(missing.type, missing.value, missing.tb))
    assert str(missing.value) == "extension namespace is not registered"
    assert secret_namespace not in traceback_text
    assert (missing.value.__cause__, missing.value.__context__) == (None, None)

    with pytest.raises(ContextProtocolError) as invalid:
        registry.get(42)  # type: ignore[arg-type]

    assert str(invalid.value) == "extension namespace is not registered"
    assert invalid.value.__cause__ is None
    assert invalid.value.__context__ is None
