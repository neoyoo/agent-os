from __future__ import annotations

import pytest

from agentos.context.models import (
    ContextBudgetExceededError,
    ContextProtocolError,
    ContextSlotProjection,
    ProjectionVariant,
)
from agentos.context.snapshot import ContextSnapshotRenderer
from agentos.context.xml import XmlElement
from tests.context._snapshot_fixtures import (
    RecordingTokenCounter,
    extension_child,
    extension_registry,
    extension_spec,
    extensions_element,
)


class ForbiddenTokenCounter:
    def count_text(self, text: str) -> int:
        raise AssertionError("counter must not be called before XML validation")


def projection(
    element: XmlElement,
    *compact: ProjectionVariant,
) -> ContextSlotProjection:
    return ContextSlotProjection(
        slot="extensions",
        owner="ContextExtensionRegistry",
        variants=(ProjectionVariant(element), *compact),
    )


def test_every_extension_variant_is_validated_against_registered_schema() -> None:
    full = extensions_element()
    extension = full.children[0]
    invalid_compact = XmlElement(
        "extensions",
        children=(
            XmlElement(
                "extension",
                extension.attributes,
                children=(XmlElement("latent-invalid-extension"),),
            ),
        ),
    )
    with pytest.raises(ContextProtocolError, match="^unregistered XML tag$"):
        ContextSnapshotRenderer(
            RecordingTokenCounter(1),
            extension_registry(),
        ).render(
            (projection(full, ProjectionVariant(invalid_compact, omitted_count=1)),),
        )


def test_extension_namespace_must_be_registered_unique_and_schema_valid() -> None:
    extension = extension_child()
    duplicate = extensions_element(extension, extension)
    renderer = ContextSnapshotRenderer(ForbiddenTokenCounter(), extension_registry())
    with pytest.raises(ContextProtocolError, match="^duplicate extension namespace$"):
        renderer.render((projection(duplicate),))

    unregistered = ContextSnapshotRenderer(ForbiddenTokenCounter())
    with pytest.raises(ContextProtocolError, match="extension namespace is not registered"):
        unregistered.render((projection(extensions_element()),))

    wrong_version = extensions_element(extension_child(version="2.0"))
    with pytest.raises(ContextProtocolError, match="extension version mismatch"):
        renderer.render((projection(wrong_version),))


def test_extension_budget_counts_each_namespace_in_its_own_complete_root() -> None:
    registry = extension_registry(
        extension_spec(),
        extension_spec("com.example.review"),
    )
    extensions = extensions_element(
        extension_child(),
        extension_child("com.example.review", text="two"),
    )
    counter = RecordingTokenCounter(1)

    ContextSnapshotRenderer(counter, registry).render((projection(extensions),))

    assert len(counter.inputs) == 2
    assert all(text.startswith("<context-snapshot ") for text in counter.inputs)
    assert all(text.endswith("</context-snapshot>\n") for text in counter.inputs)
    assert all(text.count("<extension ") == 1 for text in counter.inputs)
    assert "com.example.hitl" in counter.inputs[0]
    assert "com.example.review" in counter.inputs[1]


def test_extension_budget_is_a_hard_limit_without_global_snapshot_budget() -> None:
    registry = extension_registry(extension_spec(max_tokens=1))
    with pytest.raises(
        ContextBudgetExceededError,
        match="^context extension exceeds token budget$",
    ):
        ContextSnapshotRenderer(RecordingTokenCounter(2), registry).render(
            (projection(extensions_element()),),
        )


def test_invalid_extension_registry_is_rejected_without_content_access() -> None:
    class ForgedRegistry:
        secret = "extension-registry-secret-7f31"

    with pytest.raises(ContextProtocolError) as error:
        ContextSnapshotRenderer(
            ForbiddenTokenCounter(),
            ForgedRegistry(),  # type: ignore[arg-type]
        )
    assert str(error.value) == "context extension registry is invalid"
    assert ForgedRegistry.secret not in str(error.value)
