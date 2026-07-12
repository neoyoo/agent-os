from __future__ import annotations

import pytest

from agentos.context.models import (
    ContextBudgetExceededError,
    ContextProtocolError,
    ContextSlotProjection,
    ProjectionVariant,
)
from agentos.context.snapshot import ContextSnapshotRenderer, SnapshotBudget
from agentos.context.xml import XmlElement


class RecordingCounter:
    def __init__(self, count) -> None:  # type: ignore[no-untyped-def]
        self._count = count
        self.inputs: list[str] = []

    def count_text(self, text: str) -> int:
        self.inputs.append(text)
        return self._count(text) if callable(self._count) else self._count


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


def memory_projection_with_variants() -> ContextSlotProjection:
    memories = (
        XmlElement(
            "memory",
            (
                ("handle", "mem_1"),
                ("kind", "semantic"),
                ("category", "fact"),
                ("instructional", "false"),
            ),
            text="first memory",
        ),
        XmlElement(
            "memory",
            (
                ("handle", "mem_2"),
                ("kind", "episodic"),
                ("category", "interaction"),
                ("instructional", "false"),
            ),
            text="second memory",
        ),
    )
    full = XmlElement("memory-context", children=memories)
    compact = XmlElement("memory-context", children=(memories[0],))
    return projection(
        "memory-context",
        "MemoryRuntime",
        full,
        ProjectionVariant(compact, omitted_count=1),
    )


def working_projection() -> ContextSlotProjection:
    field = XmlElement(
        "field",
        (("name", "goal"),),
        children=(XmlElement("value", text="ship"),),
    )
    full = XmlElement(
        "working-state",
        (("schema-version", "1"),),
        children=(field,),
    )
    return projection("working-state", "ContextRuntime", full)


def test_budget_switches_only_complete_variants_and_counts_full_candidates() -> None:
    counter = RecordingCounter(lambda text: 100 if "mem_2" in text else 20)
    snapshot = ContextSnapshotRenderer(counter).render(
        (memory_projection_with_variants(),),
        SnapshotBudget(max_tokens=20),
    )
    assert "mem_1" in snapshot.xml and "mem_2" not in snapshot.xml
    assert '<truncated slot="memory-context" reason="token-budget" remaining="1"/>' in snapshot.xml
    assert len(counter.inputs) == 2
    assert all(text.startswith("<context-snapshot ") for text in counter.inputs)
    assert all(text.endswith("</context-snapshot>\n") for text in counter.inputs)
    assert "<truncated " not in counter.inputs[0]
    assert "<truncated " in counter.inputs[1]


def test_budget_trims_slots_in_registry_trim_rank_order() -> None:
    skill = XmlElement(
        "available-skills",
        (("truncated", "false"),),
        children=(
            XmlElement(
                "skill",
                (("name", "s"), ("description", "d"), ("loadable", "true"), ("trust", "trusted")),
            ),
        ),
    )
    skill_projection = projection(
        "available-skills",
        "SkillRuntime",
        skill,
        ProjectionVariant(
            XmlElement("available-skills", (("truncated", "true"),)),
            omitted_count=1,
        ),
    )
    counter = RecordingCounter(10)
    with pytest.raises(ContextBudgetExceededError):
        ContextSnapshotRenderer(counter).render(
            (memory_projection_with_variants(), skill_projection),
            SnapshotBudget(max_tokens=0),
        )
    assert "<skill " in counter.inputs[0] and "mem_2" in counter.inputs[0]
    assert "<skill " not in counter.inputs[1] and "mem_2" in counter.inputs[1]
    assert "<available-skills" not in counter.inputs[2]
    assert "mem_2" in counter.inputs[2]
    assert "mem_2" not in counter.inputs[3]


def test_noncritical_slot_can_be_omitted_with_complete_marker() -> None:
    counter = RecordingCounter(lambda text: 10 if "<memory-context" in text else 1)
    snapshot = ContextSnapshotRenderer(counter).render(
        (memory_projection_with_variants(),),
        SnapshotBudget(max_tokens=1),
    )
    assert "<memory-context" not in snapshot.xml
    assert '<truncated slot="memory-context" reason="token-budget" remaining="2"/>' in snapshot.xml


def test_critical_slot_uses_compact_variant_but_cannot_be_omitted() -> None:
    working = working_projection()
    with pytest.raises(
        ContextBudgetExceededError,
        match="^context snapshot exceeds token budget$",
    ):
        ContextSnapshotRenderer(RecordingCounter(10)).render(
            (working,),
            SnapshotBudget(max_tokens=1),
        )


def test_declared_schema_accepts_one_full_variant_and_never_trims_fields() -> None:
    field = XmlElement(
        "field",
        (("name", "goal"), ("type", "string"), ("purpose", "Goal")),
    )
    declared = XmlElement("declared-schema", (("version", "1"),), children=(field,))
    compact = XmlElement("declared-schema", (("version", "1"),))
    with pytest.raises(
        ContextProtocolError,
        match="^declared-schema requires exactly one complete variant$",
    ):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (
                projection(
                    "declared-schema",
                    "ContextRuntime",
                    declared,
                    ProjectionVariant(compact, omitted_count=1),
                ),
            ),
        )
    counter = RecordingCounter(5)
    with pytest.raises(ContextBudgetExceededError):
        ContextSnapshotRenderer(counter).render(
            (projection("declared-schema", "ContextRuntime", declared),),
            SnapshotBudget(max_tokens=4),
        )
    assert all(text.count("<field ") == 1 for text in counter.inputs)


def test_critical_only_omits_noncritical_slots_before_first_count() -> None:
    counter = RecordingCounter(1)
    snapshot = ContextSnapshotRenderer(counter).render(
        (memory_projection_with_variants(), working_projection()),
        SnapshotBudget(max_tokens=1, critical_only=True),
    )
    assert "<working-state" in snapshot.xml
    assert "<memory-context" not in snapshot.xml
    assert '<truncated slot="memory-context"' in snapshot.xml
    assert len(counter.inputs) == 1


def test_direct_small_budget_does_not_enable_critical_only() -> None:
    snapshot = ContextSnapshotRenderer(RecordingCounter(1)).render(
        (memory_projection_with_variants(),),
        SnapshotBudget(max_tokens=1),
    )
    assert "<memory-context" in snapshot.xml and "mem_2" in snapshot.xml


def test_zero_budget_rejects_even_minimal_root() -> None:
    with pytest.raises(ContextBudgetExceededError):
        ContextSnapshotRenderer(RecordingCounter(1)).render(
            (),
            SnapshotBudget(max_tokens=0),
        )


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "1", None])
def test_token_counter_invalid_return_is_mapped_to_stable_error(invalid: object) -> None:
    with pytest.raises(ContextProtocolError) as error:
        ContextSnapshotRenderer(RecordingCounter(invalid)).render(
            (),
            SnapshotBudget(max_tokens=10),
        )
    assert str(error.value) == "token counter returned invalid count"
    assert (error.value.__cause__, error.value.__context__) == (None, None)


def test_token_counter_exception_is_mapped_without_secret_cause_or_context() -> None:
    secret = "counter-exception-secret-7f31"

    class RaisingCounter:
        def count_text(self, text: str) -> int:
            raise RuntimeError(secret)

    with pytest.raises(ContextProtocolError) as error:
        ContextSnapshotRenderer(RaisingCounter()).render(
            (),
            SnapshotBudget(max_tokens=10),
        )
    assert str(error.value) == "token counter failed"
    assert secret not in str(error.value)
    assert (error.value.__cause__, error.value.__context__) == (None, None)


def test_render_rejects_invalid_budget_without_reading_projection_content() -> None:
    with pytest.raises(ContextProtocolError, match="^snapshot budget is invalid$"):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (),
            budget=object(),  # type: ignore[arg-type]
        )


def test_whole_slot_omission_remaining_is_full_root_direct_child_count() -> None:
    full = memory_projection_with_variants().variants[0].element
    counter = RecordingCounter(lambda text: 2 if "<memory-context" in text else 1)
    snapshot = ContextSnapshotRenderer(counter).render(
        (projection("memory-context", "MemoryRuntime", full),),
        SnapshotBudget(max_tokens=1),
    )
    assert len(full.children) == 2
    assert 'remaining="2"' in snapshot.xml


def test_critical_only_marks_attribute_only_slot_omission_with_zero_remaining() -> None:
    skills = XmlElement("available-skills", (("truncated", "true"),))
    snapshot = ContextSnapshotRenderer(RecordingCounter(1)).render(
        (projection("available-skills", "SkillRuntime", skills),),
        SnapshotBudget(max_tokens=1, critical_only=True),
    )
    assert "<available-skills" not in snapshot.xml
    assert (
        '<truncated slot="available-skills" reason="token-budget" remaining="0"/>'
        in snapshot.xml
    )


def test_compact_variant_switch_marks_zero_omitted_count() -> None:
    full = XmlElement(
        "available-skills",
        (("truncated", "false"),),
        children=(
            XmlElement(
                "skill",
                (
                    ("name", "schema-template"),
                    ("description", "Schema help"),
                    ("loadable", "true"),
                    ("trust", "trusted"),
                ),
            ),
        ),
    )
    compact = XmlElement("available-skills", (("truncated", "true"),))
    counter = RecordingCounter(lambda text: 10 if "<skill " in text else 1)
    snapshot = ContextSnapshotRenderer(counter).render(
        (
            projection(
                "available-skills",
                "SkillRuntime",
                full,
                ProjectionVariant(compact, omitted_count=0),
            ),
        ),
        SnapshotBudget(max_tokens=1),
    )
    assert "<available-skills" in snapshot.xml and "<skill " not in snapshot.xml
    assert (
        '<truncated slot="available-skills" reason="token-budget" remaining="0"/>'
        in snapshot.xml
    )
