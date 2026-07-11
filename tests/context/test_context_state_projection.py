import math
from collections.abc import Mapping

import pytest

from agentos.context import ContextRuntime, ContextState, WorkingStateField
from agentos.context import projection
from agentos.context.models import ContextProtocolError
from agentos.context.schema import WorkingStateSchema
from agentos.context.xml import XmlElement, render_xml


def field(name: str, type_: str = "string", purpose: str = "测试字段") -> WorkingStateField:
    return WorkingStateField(name=name, type=type_, purpose=purpose)


def snapshot_root(*children: XmlElement) -> XmlElement:
    return XmlElement(
        tag="context-snapshot",
        attributes=(
            ("protocol", "agentos.context"),
            ("version", "1.0"),
            ("origin", "runtime"),
            ("authority", "context-data"),
            ("persistence", "ephemeral"),
            ("visibility", "internal"),
        ),
        children=children,
    )


def test_project_context_state_uses_fixed_slots_tags_and_escaping() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema(
        [
            field("task_goal", purpose='目标 "<&>"'),
            field("constraints", "list[string]", "约束"),
            field("quotation", "object", "报价参数"),
        ],
    )
    runtime.update_state("task_goal", "分析 <drawing> & confirm")
    runtime.update_state("constraints", ["CNY", "说明未知值"])
    runtime.update_state(
        "quotation",
        {"quantity": 1, "currency": "CNY", "note": "<safe>"},
    )

    projections = projection.project_context_state(runtime.snapshot())

    assert [item.slot for item in projections] == ["declared-schema", "working-state"]
    assert [item.owner for item in projections] == ["ContextRuntime"] * 2
    assert [len(item.variants) for item in projections] == [1, 1]
    declared = projections[0].variants[0].element
    working = projections[1].variants[0].element
    assert declared == XmlElement(
        tag="declared-schema",
        attributes=(("version", "1"),),
        children=(
            XmlElement(
                tag="field",
                attributes=(
                    ("name", "task_goal"),
                    ("type", "string"),
                    ("purpose", '目标 "<&>"'),
                ),
            ),
            XmlElement(
                tag="field",
                attributes=(
                    ("name", "constraints"),
                    ("type", "list[string]"),
                    ("purpose", "约束"),
                ),
            ),
            XmlElement(
                tag="field",
                attributes=(
                    ("name", "quotation"),
                    ("type", "object"),
                    ("purpose", "报价参数"),
                ),
            ),
        ),
    )
    assert [item.attributes for item in working.children] == [
        (("name", "task_goal"),),
        (("name", "constraints"),),
        (("name", "quotation"),),
    ]
    rendered = render_xml(snapshot_root(declared, working))
    assert 'purpose="目标 &quot;&lt;&amp;&gt;&quot;"' in rendered
    assert "<value>分析 &lt;drawing&gt; &amp; confirm</value>" in rendered
    assert (
        '<value format="json">'
        '{"currency":"CNY","note":"&lt;safe&gt;","quantity":1}'
        "</value>"
    ) in rendered
    assert "<task_goal>" not in rendered


def test_project_context_state_renders_all_protocol_types() -> None:
    runtime = ContextRuntime()
    declarations = [
        field("text", "string"),
        field("count", "integer"),
        field("ratio", "number"),
        field("enabled", "boolean"),
        field("missing", "null"),
        field("texts", "list[string]"),
        field("counts", "list[integer]"),
        field("ratios", "list[number]"),
        field("flags", "list[boolean]"),
        field("objects", "list[object]"),
        field("metadata", "object"),
        field("empty", "list[string]"),
    ]
    values = {
        "text": "轴类零件",
        "count": 2,
        "ratio": 2.5,
        "enabled": False,
        "missing": None,
        "texts": ["A", "B"],
        "counts": [1, 2],
        "ratios": [1, 2.5],
        "flags": [True, False],
        "objects": [{"z": 2, "a": 1}, {"nested": [True, None]}],
        "metadata": {"z": 2, "a": {"b": 1}},
        "empty": [],
    }
    runtime.declare_schema(declarations)
    for name, value in values.items():
        runtime.update_state(name, value)

    working = projection.project_context_state(runtime.snapshot())[1].variants[0].element
    by_name = {dict(item.attributes)["name"]: item for item in working.children}

    assert by_name["text"].children == (XmlElement("value", text="轴类零件"),)
    assert by_name["count"].children == (XmlElement("value", text="2"),)
    assert by_name["ratio"].children == (XmlElement("value", text="2.5"),)
    assert by_name["enabled"].children == (XmlElement("value", text="false"),)
    assert by_name["missing"].children == (
        XmlElement("value", attributes=(("null", "true"),), text=""),
    )
    assert [item.text for item in by_name["texts"].children] == ["A", "B"]
    assert [item.text for item in by_name["counts"].children] == ["1", "2"]
    assert [item.text for item in by_name["ratios"].children] == ["1", "2.5"]
    assert [item.text for item in by_name["flags"].children] == ["true", "false"]
    assert [item.text for item in by_name["objects"].children] == [
        '{"a":1,"z":2}',
        '{"nested":[true,null]}',
    ]
    assert by_name["objects"].children[0].tag == "item"
    assert by_name["objects"].children[0].attributes == ()
    assert by_name["metadata"].children == (
        XmlElement(
            "value",
            attributes=(("format", "json"),),
            text='{"a":{"b":1},"z":2}',
        ),
    )
    assert by_name["empty"].children == ()


def test_empty_and_unassigned_state_omit_empty_slots() -> None:
    assert projection.project_context_state(ContextState()) == ()

    runtime = ContextRuntime()
    runtime.declare_schema([field("first"), field("second")])
    projections = projection.project_context_state(runtime.snapshot())
    assert [item.slot for item in projections] == ["declared-schema"]

    runtime.update_state("second", "assigned later")
    working = projection.project_context_state(runtime.snapshot())[1].variants[0].element
    assert [dict(item.attributes)["name"] for item in working.children] == ["second"]


def test_project_context_state_does_not_claim_other_owner_slots() -> None:
    state = ContextState(
        compressed_history=[],
        inherited_state=["legacy inherited state"],
        memory_context=["legacy memory"],
        runtime_notices=["legacy notice"],
    )

    assert projection.project_context_state(state) == ()


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("a", True),
        ("A_" + "0" * 62, True),
        ("A" * 64, True),
        ("A" * 65, False),
        ("9goal", False),
        ("bad-name", False),
        ("bad</field>", False),
        ("字段", False),
    ],
)
def test_schema_field_name_boundaries(name: str, valid: bool) -> None:
    runtime = ContextRuntime()
    if valid:
        runtime.declare_schema([field(name)])
        assert runtime.state.working_state_schema.fields[0].name == name
    else:
        with pytest.raises(ContextProtocolError, match="field name"):
            runtime.declare_schema([field(name)])


@pytest.mark.parametrize(
    ("purpose", "valid"),
    [("", True), ("x", True), ("界" * 300, True), ("界" * 301, False)],
)
def test_schema_purpose_boundaries(purpose: str, valid: bool) -> None:
    runtime = ContextRuntime()
    if valid:
        runtime.declare_schema([field("goal", purpose=purpose)])
        assert runtime.state.working_state_schema.fields[0].purpose == purpose
    else:
        with pytest.raises(ContextProtocolError, match="field purpose"):
            runtime.declare_schema([field("goal", purpose=purpose)])


@pytest.mark.parametrize("legacy_type", ["str", "list[str]", "obj", "String"])
def test_schema_rejects_legacy_and_unknown_types(legacy_type: str) -> None:
    with pytest.raises(ContextProtocolError, match="field type"):
        ContextRuntime().declare_schema([field("goal", legacy_type)])


@pytest.mark.parametrize(
    "invalid_field",
    [
        object(),
        WorkingStateField(name=1, type="string", purpose="purpose"),
        WorkingStateField(name="goal", type=1, purpose="purpose"),
        WorkingStateField(name="goal", type="string", purpose=1),
    ],
)
def test_schema_requires_strict_field_objects_and_strings(invalid_field: object) -> None:
    with pytest.raises(ContextProtocolError, match="working state field"):
        ContextRuntime().declare_schema([invalid_field])  # type: ignore[list-item]


def test_schema_rejects_duplicate_names() -> None:
    with pytest.raises(ContextProtocolError, match="duplicate field"):
        ContextRuntime().declare_schema([field("goal"), field("goal")])


@pytest.mark.parametrize(
    ("field_type", "invalid_value"),
    [
        ("string", 1),
        ("integer", True),
        ("integer", 1.0),
        ("number", True),
        ("number", "1"),
        ("boolean", 1),
        ("null", False),
        ("list[string]", "abc"),
        ("list[string]", [1]),
        ("list[integer]", [True]),
        ("list[number]", [False]),
        ("list[boolean]", [1]),
        ("list[object]", ["not-object"]),
        ("object", []),
    ],
)
def test_update_state_rejects_cross_type_values(
    field_type: str,
    invalid_value: object,
) -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("value", field_type)])

    with pytest.raises(ContextProtocolError, match="field value"):
        runtime.update_state("value", invalid_value)  # type: ignore[arg-type]

    assert runtime.state.working_state == {}


@pytest.mark.parametrize("invalid_number", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field_type", ["number", "list[number]"])
def test_update_state_rejects_non_finite_numbers(
    field_type: str,
    invalid_number: float,
) -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("value", field_type)])
    value: object = invalid_number if field_type == "number" else [invalid_number]

    with pytest.raises(ContextProtocolError, match="finite"):
        runtime.update_state("value", value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid_object",
    [
        {1: "non-string key"},
        {"nested": {2: "non-string key"}},
        {"nested": [object()]},
        {"nested": math.nan},
    ],
)
def test_update_state_rejects_invalid_nested_json_before_freezing(
    invalid_object: Mapping[object, object],
) -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("metadata", "object")])

    with pytest.raises(ContextProtocolError, match="JSON-compatible|finite"):
        runtime.update_state("metadata", invalid_object)  # type: ignore[arg-type]

    assert runtime.state.working_state == {}


@pytest.mark.parametrize(
    "working_state",
    [
        {"metadata": {1: "bad"}},
        {"metadata": {"nested": {2: "bad"}}},
    ],
)
def test_direct_context_state_rejects_non_string_object_keys(
    working_state: Mapping[str, object],
) -> None:
    with pytest.raises(ContextProtocolError, match="object keys must be strings"):
        ContextState(working_state=working_state)  # type: ignore[arg-type]


def test_direct_context_state_rejects_non_string_field_names() -> None:
    with pytest.raises(ContextProtocolError, match="field name must be a string"):
        ContextState(working_state={1: "bad"})  # type: ignore[dict-item]


@pytest.mark.parametrize("container_kind", ["list", "dict"])
def test_direct_context_state_rejects_cycles(container_kind: str) -> None:
    if container_kind == "list":
        value: object = []
        value.append(value)  # type: ignore[union-attr]
    else:
        value = {}
        value["self"] = value  # type: ignore[index]

    with pytest.raises(ContextProtocolError, match="cycles"):
        ContextState(working_state={"value": value})  # type: ignore[arg-type]


def test_direct_context_state_rejects_incompatible_values_stably() -> None:
    with pytest.raises(ContextProtocolError, match="JSON-compatible"):
        ContextState(working_state={"value": object()})  # type: ignore[arg-type]


def test_direct_context_state_allows_shared_non_cyclic_substructures() -> None:
    shared = {"count": 1}

    state = ContextState(working_state={"items": [shared, shared]})

    assert state.working_state["items"] == ({"count": 1}, {"count": 1})


def test_projection_defensively_validates_direct_context_state_construction() -> None:
    invalid_schema = ContextState(
        working_state_schema=WorkingStateSchema(fields=(field("goal", "str"),)),
    )
    with pytest.raises(ContextProtocolError, match="field type"):
        projection.project_context_state(invalid_schema)

    undeclared_state = ContextState(working_state={"goal": "value"})
    with pytest.raises(ContextProtocolError, match="not declared"):
        projection.project_context_state(undeclared_state)

    wrong_value = ContextState(
        working_state_schema=WorkingStateSchema(fields=(field("goal"),)),
        working_state={"goal": 1},
    )
    with pytest.raises(ContextProtocolError, match="field value"):
        projection.project_context_state(wrong_value)


def test_object_projection_is_deterministic_across_mapping_order() -> None:
    def projected_text(value: dict[str, object]) -> str:
        runtime = ContextRuntime()
        runtime.declare_schema([field("metadata", "object")])
        runtime.update_state("metadata", value)
        working = projection.project_context_state(runtime.snapshot())[1]
        return working.variants[0].element.children[0].children[0].text or ""

    assert projected_text({"z": 1, "a": {"y": 2, "b": 3}}) == projected_text(
        {"a": {"b": 3, "y": 2}, "z": 1},
    )
