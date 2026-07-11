from __future__ import annotations

from inspect import Parameter, signature
from pathlib import Path

import pytest

from agentos.context.models import (
    ContextProtocolError,
    ContextSlotProjection,
    ProjectionVariant,
)
from agentos.context.registry import (
    CONTEXT_SLOT_REGISTRY,
)
from agentos.context.snapshot import ContextSnapshotRenderer, SnapshotBudget
from agentos.context.xml import XmlElement
from tests.context._snapshot_fixtures import (
    RecordingTokenCounter,
    extension_registry,
    extensions_element,
)


class ForbiddenTokenCounter:
    def count_text(self, text: str) -> int:
        raise AssertionError("unbudgeted rendering must not count tokens")


def projection(
    slot: str,
    owner: str,
    element: XmlElement,
    *compact: ProjectionVariant,
) -> ContextSlotProjection:
    return ContextSlotProjection(
        slot=slot,  # type: ignore[arg-type]
        owner=owner,
        variants=(ProjectionVariant(element), *compact),
    )


def core_elements() -> dict[str, XmlElement]:
    return {
        "declared-schema": XmlElement(
            "declared-schema",
            (("version", "1"),),
            children=(
                XmlElement(
                    "field",
                    (("name", "task_goal"), ("type", "string"), ("purpose", "Goal")),
                ),
            ),
        ),
        "working-state": XmlElement(
            "working-state",
            (("schema-version", "1"),),
            children=(
                XmlElement(
                    "field",
                    (("name", "task_goal"),),
                    children=(XmlElement("value", text="Quote <shaft> 零件"),),
                ),
            ),
        ),
        "active-plan": XmlElement(
            "active-plan",
            (("status", "in-progress"),),
            children=(
                XmlElement("goal", text="Prepare quotation"),
                XmlElement(
                    "step",
                    (("handle", "step_1"), ("status", "pending")),
                    text="Estimate machining",
                ),
            ),
        ),
        "inherited-state": XmlElement(
            "inherited-state",
            children=(XmlElement("item", (("kind", "fact"),), text="Currency is CNY"),),
        ),
        "compressed-history": XmlElement(
            "compressed-history",
            children=(
                XmlElement(
                    "segment",
                    (("handle", "seg_1"), ("topic", "requirements"), ("recallable", "true")),
                    text="User requested a process route.",
                ),
            ),
        ),
        "memory-context": XmlElement(
            "memory-context",
            children=(
                XmlElement(
                    "memory",
                    (
                        ("handle", "mem_1"),
                        ("kind", "semantic"),
                        ("category", "preference"),
                        ("instructional", "false"),
                    ),
                    text="User prefers Chinese technical discussion.",
                ),
            ),
        ),
        "available-skills": XmlElement(
            "available-skills",
            (("truncated", "false"),),
            children=(
                XmlElement(
                    "skill",
                    (
                        ("name", "drawing-quotation"),
                        ("description", "Analyze drawings and prepare quotations"),
                        ("loadable", "true"),
                        ("trust", "trusted"),
                    ),
                ),
            ),
        ),
        "artifact-catalog": XmlElement(
            "artifact-catalog",
            (("scope", "session"), ("truncated", "false")),
            children=(
                XmlElement(
                    "artifact",
                    (
                        ("handle", "art_1"),
                        ("filename", "drawing.png"),
                        ("media-type", "image/png"),
                        ("state", "available"),
                    ),
                ),
            ),
        ),
        "extensions": extensions_element(),
    }


def all_projections() -> tuple[ContextSlotProjection, ...]:
    elements = core_elements()
    return tuple(
        projection(slot, spec.owner, elements[slot])
        for slot, spec in CONTEXT_SLOT_REGISTRY.items()
    )


def empty_element(slot: str) -> XmlElement:
    attributes = {
        "declared-schema": (("version", "1"),),
        "working-state": (("schema-version", "1"),),
        "active-plan": (("status", "pending"),),
        "available-skills": (("truncated", "true"),),
        "artifact-catalog": (("scope", "session"), ("truncated", "true")),
    }.get(slot, ())
    return XmlElement(slot, attributes)


def test_snapshot_budget_from_request_budget_is_exact_and_tracks_critical_mode() -> None:
    assert SnapshotBudget.from_request_budget(48_000) == SnapshotBudget(
        max_tokens=12_000,
        critical_only=False,
    )
    assert SnapshotBudget.from_request_budget(2_048) == SnapshotBudget(
        max_tokens=512,
        critical_only=False,
    )
    assert SnapshotBudget.from_request_budget(2_047) == SnapshotBudget(
        max_tokens=511,
        critical_only=True,
    )
    assert SnapshotBudget.from_request_budget(0) == SnapshotBudget(
        max_tokens=0,
        critical_only=True,
    )


@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "10", None])
def test_snapshot_budget_rejects_invalid_values_without_echo(invalid: object) -> None:
    with pytest.raises(ContextProtocolError) as error:
        SnapshotBudget(max_tokens=invalid)  # type: ignore[arg-type]
    assert str(error.value) == "snapshot budget is invalid"
    assert repr(invalid) not in str(error.value)

    with pytest.raises(ContextProtocolError) as request_error:
        SnapshotBudget.from_request_budget(invalid)  # type: ignore[arg-type]
    assert str(request_error.value) == "remaining input token budget is invalid"
    assert repr(invalid) not in str(request_error.value)


def test_direct_budget_does_not_infer_critical_mode_from_max_tokens() -> None:
    assert SnapshotBudget(max_tokens=1).critical_only is False
    with pytest.raises(ContextProtocolError, match="snapshot budget is invalid"):
        SnapshotBudget(max_tokens=1, critical_only=1)  # type: ignore[arg-type]


def test_minimal_snapshot_matches_golden_without_counting_tokens() -> None:
    snapshot = ContextSnapshotRenderer(ForbiddenTokenCounter()).render(())
    golden = Path(__file__).with_name("goldens") / "context-snapshot-v1-minimal.xml"
    assert snapshot.xml == golden.read_text(encoding="utf-8")
    assert snapshot.xml.endswith("\n") and not snapshot.xml.endswith("\n\n")


def test_full_snapshot_orders_slots_and_matches_utf8_golden() -> None:
    expected_order = tuple(CONTEXT_SLOT_REGISTRY)
    shuffled = tuple(reversed(all_projections()))
    snapshot = ContextSnapshotRenderer(
        RecordingTokenCounter(1),
        extension_registry(),
    ).render(shuffled)
    golden = Path(__file__).with_name("goldens") / "context-snapshot-v1-full.xml"

    assert snapshot.xml == golden.read_text(encoding="utf-8")
    assert snapshot.xml.encode() == golden.read_bytes()
    positions = [snapshot.xml.index(f"<{slot}") for slot in expected_order]
    assert positions == sorted(positions)
    for forbidden in ("data:", "file-", "x-amz-signature", "C:\\", "/tmp/"):
        assert forbidden not in snapshot.xml.casefold()


def test_same_inputs_are_byte_deterministic() -> None:
    renderer = ContextSnapshotRenderer(RecordingTokenCounter(1), extension_registry())
    first = renderer.render(all_projections()).xml.encode("utf-8")
    second = renderer.render(tuple(reversed(all_projections()))).xml.encode("utf-8")
    assert first == second


@pytest.mark.parametrize("slot", tuple(CONTEXT_SLOT_REGISTRY))
def test_each_registered_slot_uses_its_wire_root_tag(slot: str) -> None:
    spec = CONTEXT_SLOT_REGISTRY[slot]  # type: ignore[index]
    counter = RecordingTokenCounter(1) if slot == "extensions" else ForbiddenTokenCounter()
    renderer = ContextSnapshotRenderer(counter, extension_registry())
    snapshot = renderer.render((projection(slot, spec.owner, core_elements()[slot]),))
    assert f"  <{slot}" in snapshot.xml


@pytest.mark.parametrize("slot", tuple(CONTEXT_SLOT_REGISTRY))
def test_submitted_valid_slot_is_preserved_without_children(slot: str) -> None:
    spec = CONTEXT_SLOT_REGISTRY[slot]  # type: ignore[index]
    renderer = ContextSnapshotRenderer(ForbiddenTokenCounter(), extension_registry())
    snapshot = renderer.render((projection(slot, spec.owner, empty_element(slot)),))
    assert f"<{slot}" in snapshot.xml
    assert snapshot.xml.startswith("<context-snapshot ")


def test_renderer_constructor_and_render_signatures_are_frozen() -> None:
    constructor = signature(ContextSnapshotRenderer).parameters
    assert tuple(constructor) == ("token_counter", "extension_registry")
    assert constructor["token_counter"].default is Parameter.empty
    assert constructor["extension_registry"].default is None
    assert tuple(signature(ContextSnapshotRenderer.render).parameters) == (
        "self",
        "projections",
        "budget",
    )


def test_duplicate_slot_owner_mismatch_and_root_mismatch_are_rejected() -> None:
    working = all_projections()[1]
    renderer = ContextSnapshotRenderer(ForbiddenTokenCounter())
    with pytest.raises(ContextProtocolError, match="^duplicate context slot$"):
        renderer.render((working, working))
    with pytest.raises(ContextProtocolError, match="^context slot owner mismatch$"):
        renderer.render(
            (projection("working-state", "MemoryRuntime", working.variants[0].element),),
        )
    with pytest.raises(ContextProtocolError, match="^context slot root tag mismatch$"):
        renderer.render(
            (
                projection(
                    "working-state",
                    "ContextRuntime",
                    core_elements()["memory-context"],
                ),
            ),
        )


def test_invalid_projection_and_variant_use_stable_non_echoing_errors() -> None:
    secret = "projection-secret-7f31"
    renderer = ContextSnapshotRenderer(ForbiddenTokenCounter())
    with pytest.raises(ContextProtocolError) as dto_error:
        renderer.render((secret,))  # type: ignore[arg-type]
    assert str(dto_error.value) == "context slot projection is invalid"
    assert secret not in str(dto_error.value)

    valid = all_projections()[1]
    forged = object.__new__(ContextSlotProjection)
    object.__setattr__(forged, "slot", valid.slot)
    object.__setattr__(forged, "owner", valid.owner)
    object.__setattr__(forged, "variants", (secret,))
    with pytest.raises(ContextProtocolError) as variant_error:
        renderer.render((forged,))
    assert str(variant_error.value) == "projection variant is invalid"
    assert secret not in str(variant_error.value)


def test_projection_iterable_exception_is_mapped_without_cause_or_context() -> None:
    secret = "projection-iteration-secret-7f31"

    class RaisingProjections:
        def __iter__(self):  # type: ignore[no-untyped-def]
            raise LookupError(secret)

    with pytest.raises(ContextProtocolError) as error:
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            RaisingProjections(),
        )
    assert str(error.value) == "context slot projection is invalid"
    assert secret not in str(error.value)
    assert (error.value.__cause__, error.value.__context__) == (None, None)


def test_projection_iterable_does_not_swallow_base_exception() -> None:
    class InterruptingProjections:
        def __iter__(self):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            InterruptingProjections(),
        )


@pytest.mark.parametrize(
    "omitted_counts",
    [
        (1,),
        (0, 2, 1),
    ],
)
def test_projection_variant_omitted_counts_are_complete_and_monotonic(
    omitted_counts: tuple[int, ...],
) -> None:
    element = core_elements()["memory-context"]
    variants = tuple(
        ProjectionVariant(element, omitted_count=count)
        for count in omitted_counts
    )
    invalid = ContextSlotProjection(
        slot="memory-context",
        owner="MemoryRuntime",
        variants=variants,
    )
    with pytest.raises(
        ContextProtocolError,
        match="^projection omitted counts are invalid$",
    ):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render((invalid,))


def test_equal_projection_variant_omitted_counts_are_allowed() -> None:
    element = core_elements()["memory-context"]
    snapshot = ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
        (
            projection(
                "memory-context",
                "MemoryRuntime",
                element,
                ProjectionVariant(element, omitted_count=0),
            ),
        ),
    )
    assert "<memory-context>" in snapshot.xml


def test_every_core_variant_is_validated_in_a_complete_root() -> None:
    full = core_elements()["working-state"]
    invalid_compact = XmlElement(
        "working-state",
        (("schema-version", "1"),),
        children=(XmlElement("value", text="latent-invalid-variant"),),
    )
    with pytest.raises(ContextProtocolError, match="^child tag is not allowed$"):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (
                projection(
                    "working-state",
                    "ContextRuntime",
                    full,
                    ProjectionVariant(invalid_compact, omitted_count=1),
                ),
            ),
        )
