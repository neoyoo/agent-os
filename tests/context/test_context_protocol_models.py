from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from agentos.context.models import (
    CONTEXT_PROTOCOL,
    CONTEXT_PROTOCOL_VERSION,
    AllowedContentPartKind,
    ContextBudgetExceededError,
    ContextProtocolError,
    ContextProtocolVersionError,
    ContextSensitiveDataError,
    ContextSlotName,
    ContextSlotProjection,
    ContextSnapshot,
    ProjectionVariant,
    RuntimeContract,
    RuntimeDirective,
    RuntimeDirectiveKind,
    SystemEnvelope,
    SystemSectionName,
    TrustedSkillInstruction,
    validate_protocol_version,
)


def test_protocol_identifiers_and_literal_values_are_stable() -> None:
    assert CONTEXT_PROTOCOL == "agentos.context"
    assert CONTEXT_PROTOCOL_VERSION == "1.0"
    assert set(get_args(SystemSectionName)) == {
        "runtime_contract",
        "interaction_protocol",
        "context_management_rules",
        "runtime_directives",
        "trusted_skill_instructions",
        "workspace_contract",
    }
    assert set(get_args(ContextSlotName)) == {
        "declared-schema",
        "working-state",
        "active-plan",
        "inherited-state",
        "compressed-history",
        "memory-context",
        "available-skills",
        "artifact-catalog",
        "extensions",
    }


def test_context_snapshot_uses_protocol_defaults() -> None:
    snapshot = ContextSnapshot(xml="<context-snapshot/>\n")

    assert snapshot.protocol == "agentos.context"
    assert snapshot.version == "1.0"


def test_context_snapshot_protocol_cannot_be_overridden() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'protocol'"):
        ContextSnapshot(  # type: ignore[call-arg]
            xml="<context-snapshot/>\n",
            protocol="wrong",
        )


def test_context_snapshot_version_cannot_be_overridden() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'version'"):
        ContextSnapshot(  # type: ignore[call-arg]
            xml="<context-snapshot/>\n",
            version="9.9",
        )


@pytest.mark.parametrize(
    ("value", "attribute", "replacement"),
    [
        (SystemEnvelope(text="trusted"), "text", "changed"),
        (ContextSnapshot(xml="<context-snapshot/>\n"), "xml", "changed"),
        (
            RuntimeContract(identity="AgentOS", security_guardrails=("safe",)),
            "identity",
            "changed",
        ),
        (
            RuntimeDirective(kind=RuntimeDirectiveKind.BACKGROUND_TOOL_RUNNING),
            "kind",
            RuntimeDirectiveKind.AWAITING_APPROVAL,
        ),
        (
            TrustedSkillInstruction(skill_id="skill", text="trusted"),
            "text",
            "changed",
        ),
        (ProjectionVariant(element=object()), "omitted_count", 1),
        (
            ContextSlotProjection(
                slot="working-state",
                owner="ContextRuntime",
                variants=(ProjectionVariant(element=object()),),
            ),
            "owner",
            "changed",
        ),
    ],
)
def test_protocol_value_types_are_frozen_and_slotted(
    value: object,
    attribute: str,
    replacement: object,
) -> None:
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(value, attribute, replacement)


def test_runtime_contract_normalizes_sequences_to_tuples() -> None:
    guardrails = ["do not expose secrets"]
    additional_rules = ["keep protocol identifiers in English"]

    contract = RuntimeContract(
        identity="AgentOS",
        security_guardrails=guardrails,  # type: ignore[arg-type]
        additional_rules=additional_rules,  # type: ignore[arg-type]
    )
    guardrails.append("mutated")
    additional_rules.append("mutated")

    assert contract.security_guardrails == ("do not expose secrets",)
    assert contract.additional_rules == (
        "keep protocol identifiers in English",
    )


def test_runtime_contract_defaults_additional_rules_to_empty_tuple() -> None:
    contract = RuntimeContract(identity="AgentOS", security_guardrails=())

    assert contract.additional_rules == ()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_message", "sensitive_text"),
    [
        (
            "security_guardrails",
            "sensitive-guardrail",
            "runtime contract security_guardrails must be a sequence of strings",
            "sensitive-guardrail",
        ),
        (
            "security_guardrails",
            b"sensitive-guardrail",
            "runtime contract security_guardrails must be a sequence of strings",
            "sensitive-guardrail",
        ),
        (
            "additional_rules",
            "sensitive-rule",
            "runtime contract additional_rules must be a sequence of strings",
            "sensitive-rule",
        ),
        (
            "additional_rules",
            b"sensitive-rule",
            "runtime contract additional_rules must be a sequence of strings",
            "sensitive-rule",
        ),
    ],
)
def test_runtime_contract_rejects_string_like_sequence_containers(
    field_name: str,
    invalid_value: object,
    expected_message: str,
    sensitive_text: str,
) -> None:
    arguments: dict[str, object] = {
        "identity": "AgentOS",
        "security_guardrails": (),
        "additional_rules": (),
    }
    arguments[field_name] = invalid_value

    with pytest.raises(ContextProtocolError) as error:
        RuntimeContract(**arguments)  # type: ignore[arg-type]

    assert str(error.value) == expected_message
    assert sensitive_text not in str(error.value)


@pytest.mark.parametrize(
    ("field_name", "expected_message"),
    [
        (
            "security_guardrails",
            "runtime contract security_guardrails must contain only strings",
        ),
        (
            "additional_rules",
            "runtime contract additional_rules must contain only strings",
        ),
    ],
)
def test_runtime_contract_rejects_non_string_sequence_items(
    field_name: str,
    expected_message: str,
) -> None:
    sensitive_value = 8675309
    arguments: dict[str, object] = {
        "identity": "AgentOS",
        "security_guardrails": (),
        "additional_rules": (),
    }
    arguments[field_name] = ["valid", sensitive_value]

    with pytest.raises(ContextProtocolError) as error:
        RuntimeContract(**arguments)  # type: ignore[arg-type]

    assert str(error.value) == expected_message
    assert str(sensitive_value) not in str(error.value)


def test_context_slot_projection_normalizes_non_empty_variants() -> None:
    variant = ProjectionVariant(element=object())
    variants = [variant]

    projection = ContextSlotProjection(
        slot="working-state",
        owner="ContextRuntime",
        variants=variants,  # type: ignore[arg-type]
    )
    variants.append(ProjectionVariant(element=object(), omitted_count=1))

    assert projection.variants == (variant,)


def test_context_slot_projection_rejects_non_variant_items() -> None:
    sensitive_value = "sensitive-projection-value"

    with pytest.raises(ContextProtocolError) as error:
        ContextSlotProjection(
            slot="working-state",
            owner="ContextRuntime",
            variants=[sensitive_value],  # type: ignore[list-item, arg-type]
        )

    assert str(error.value) == (
        "context slot variants must contain only ProjectionVariant"
    )
    assert sensitive_value not in str(error.value)


def test_context_slot_projection_rejects_empty_variants() -> None:
    with pytest.raises(
        ContextProtocolError,
        match="^context slot requires at least one variant$",
    ):
        ContextSlotProjection(
            slot="working-state",
            owner="ContextRuntime",
            variants=(),
        )


def test_context_protocol_errors_share_one_value_error_hierarchy() -> None:
    assert issubclass(ContextProtocolError, ValueError)
    assert issubclass(ContextProtocolVersionError, ContextProtocolError)
    assert issubclass(ContextBudgetExceededError, ContextProtocolError)
    assert issubclass(ContextSensitiveDataError, ContextProtocolError)


def test_runtime_directive_enum_values_are_stable() -> None:
    assert tuple(item.value for item in RuntimeDirectiveKind) == (
        "background_tool_running",
        "awaiting_approval",
        "unsupported_content_part",
    )
    assert tuple(item.value for item in AllowedContentPartKind) == (
        "image",
        "file",
        "audio",
    )


@pytest.mark.parametrize(
    ("kind", "content_part_kind"),
    [
        (RuntimeDirectiveKind.BACKGROUND_TOOL_RUNNING, None),
        (RuntimeDirectiveKind.AWAITING_APPROVAL, None),
        (
            RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART,
            AllowedContentPartKind.IMAGE,
        ),
        (
            RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART,
            AllowedContentPartKind.FILE,
        ),
        (
            RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART,
            AllowedContentPartKind.AUDIO,
        ),
    ],
)
def test_runtime_directive_accepts_only_valid_combinations(
    kind: RuntimeDirectiveKind,
    content_part_kind: AllowedContentPartKind | None,
) -> None:
    directive = RuntimeDirective(
        kind=kind,
        content_part_kind=content_part_kind,
    )

    assert directive.kind is kind
    assert directive.content_part_kind is content_part_kind


@pytest.mark.parametrize(
    "kind",
    [
        RuntimeDirectiveKind.BACKGROUND_TOOL_RUNNING,
        RuntimeDirectiveKind.AWAITING_APPROVAL,
    ],
)
def test_non_content_runtime_directives_reject_content_part_kind(
    kind: RuntimeDirectiveKind,
) -> None:
    with pytest.raises(
        ContextProtocolError,
        match="^runtime directive kind does not accept content part kind$",
    ):
        RuntimeDirective(kind=kind, content_part_kind=AllowedContentPartKind.IMAGE)


def test_unsupported_content_directive_requires_content_part_kind() -> None:
    with pytest.raises(
        ContextProtocolError,
        match=(
            "^unsupported content part directive requires "
            "AllowedContentPartKind$"
        ),
    ):
        RuntimeDirective(kind=RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART)


def test_runtime_directive_rejects_free_string_kind_without_echoing_it() -> None:
    sensitive_kind = "secret-directive-kind"

    with pytest.raises(ContextProtocolError) as error:
        RuntimeDirective(kind=sensitive_kind)  # type: ignore[arg-type]

    assert str(error.value) == "runtime directive kind must be RuntimeDirectiveKind"
    assert sensitive_kind not in str(error.value)


def test_runtime_directive_rejects_free_string_part_without_echoing_it() -> None:
    sensitive_part = "secret-content-part"

    with pytest.raises(ContextProtocolError) as error:
        RuntimeDirective(
            kind=RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART,
            content_part_kind=sensitive_part,  # type: ignore[arg-type]
        )

    assert str(error.value) == (
        "unsupported content part directive requires AllowedContentPartKind"
    )
    assert sensitive_part not in str(error.value)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.999"])
def test_protocol_version_accepts_supported_major(version: str) -> None:
    assert validate_protocol_version(version) is None


@pytest.mark.parametrize(
    "version",
    ["2.0", "1.", "1.x", "01.0", "", " 1.0", "1.0 "],
)
def test_protocol_version_rejects_unknown_major_or_invalid_format(
    version: str,
) -> None:
    with pytest.raises(
        ContextProtocolVersionError,
        match="unsupported major context protocol version",
    ):
        validate_protocol_version(version)
