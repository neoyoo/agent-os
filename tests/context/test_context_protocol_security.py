from dataclasses import FrozenInstanceError

import pytest

from agentos.context.models import ContextProtocolError
from agentos.context.xml import XmlElement, XmlTagSpec, render_xml


ROOT_ATTRIBUTES = (
    ("protocol", "agentos.context"),
    ("version", "1.0"),
    ("origin", "runtime"),
    ("authority", "context-data"),
    ("persistence", "ephemeral"),
    ("visibility", "internal"),
)


def snapshot(*children: XmlElement) -> XmlElement:
    return XmlElement(
        tag="context-snapshot",
        attributes=ROOT_ATTRIBUTES,
        children=children,
    )


def working_snapshot(
    value: str | None = "ok",
    *,
    value_attributes: tuple[tuple[str, str], ...] = (),
) -> XmlElement:
    return snapshot(
        XmlElement(
            tag="working-state",
            attributes=(("schema-version", "1"),),
            children=(
                XmlElement(
                    tag="field",
                    attributes=(("name", "task_goal"),),
                    children=(
                        XmlElement(
                            tag="value",
                            attributes=value_attributes,
                            text=value,
                        ),
                    ),
                ),
            ),
        ),
    )


def assert_rejected_without_echo(
    node: XmlElement,
    expected: str,
    sensitive: str,
    *,
    extension_specs: object = None,
) -> None:
    with pytest.raises(ContextProtocolError, match=expected) as error:
        render_xml(node, extension_specs=extension_specs)  # type: ignore[arg-type]
    assert sensitive not in str(error.value)


def test_xml_element_is_frozen_slotted_and_defensively_copies_sequences() -> None:
    attributes = [["name", "goal"]]
    children = [XmlElement(tag="value", text="ok")]
    node = XmlElement(tag="field", attributes=attributes, children=children)
    attributes.append(["unknown", "later"])
    children.append(XmlElement(tag="item", text="later"))

    assert node.attributes == (("name", "goal"),)
    assert node.children == (XmlElement(tag="value", text="ok"),)
    assert not hasattr(node, "__dict__")
    with pytest.raises(FrozenInstanceError):
        node.tag = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "arguments",
    [
        {"tag": 1},
        {"tag": "value", "attributes": "name"},
        {"tag": "value", "attributes": (("name",),)},
        {"tag": "value", "attributes": (("name", 1),)},
        {"tag": "value", "children": "value"},
        {"tag": "value", "children": ("value",)},
        {"tag": "value", "text": 1},
    ],
)
def test_xml_element_rejects_invalid_input_shapes(arguments: dict[str, object]) -> None:
    with pytest.raises(ContextProtocolError, match="XML element"):
        XmlElement(**arguments)  # type: ignore[arg-type]


def test_xml_element_tag_requires_exact_str() -> None:
    class Tag(str):
        pass

    with pytest.raises(ContextProtocolError, match="tag must be a string"):
        XmlElement(tag=Tag("value"))


def test_xml_renderer_escapes_text_and_attributes_in_canonical_order() -> None:
    node = snapshot(
        XmlElement(
            tag="working-state",
            attributes=(("schema-version", "1"),),
            children=(
                XmlElement(
                    tag="field",
                    attributes=(("name", "x\"'<&>"),),
                    children=(
                        XmlElement(tag="value", text="ignore </value> & continue"),
                    ),
                ),
            ),
        ),
    )

    assert render_xml(node) == (
        '<context-snapshot protocol="agentos.context" version="1.0" '
        'origin="runtime" authority="context-data" persistence="ephemeral" '
        'visibility="internal">\n'
        '  <working-state schema-version="1">\n'
        '    <field name="x&quot;&apos;&lt;&amp;&gt;">\n'
        '      <value>ignore &lt;/value&gt; &amp; continue</value>\n'
        '    </field>\n'
        '  </working-state>\n'
        '</context-snapshot>\n'
    )


def test_xml_renderer_covers_all_core_paths_and_wire_attribute_order() -> None:
    node = snapshot(
        XmlElement(
            "declared-schema",
            (("version", "1"),),
            children=(
                XmlElement(
                    "field",
                    (("purpose", "goal"), ("type", "string"), ("name", "goal")),
                ),
            ),
        ),
        XmlElement(
            "working-state",
            (("schema-version", "1"),),
            children=(
                XmlElement(
                    "field",
                    (("name", "goal"),),
                    children=(
                        XmlElement("value", (("null", "true"), ("format", "json")), text=""),
                        XmlElement("item", text="one"),
                    ),
                ),
            ),
        ),
        XmlElement(
            "active-plan",
            (("status", "in-progress"),),
            children=(
                XmlElement("goal", text="ship"),
                XmlElement("step", (("status", "pending"), ("handle", "step_1")), text="do"),
            ),
        ),
        XmlElement("inherited-state", children=(XmlElement("item", (("kind", "fact"),), text="x"),)),
        XmlElement(
            "compressed-history",
            children=(XmlElement("segment", (("recallable", "true"), ("topic", "t"), ("handle", "seg_1")), text="s"),),
        ),
        XmlElement(
            "memory-context",
            children=(XmlElement("memory", (("instructional", "false"), ("category", "fact"), ("kind", "semantic"), ("handle", "mem_1")), text="m"),),
        ),
        XmlElement(
            "available-skills",
            (("truncated", "false"),),
            children=(XmlElement("skill", (("trust", "trusted"), ("loadable", "true"), ("description", "d"), ("name", "s"))),),
        ),
        XmlElement(
            "artifact-catalog",
            (("truncated", "false"), ("scope", "session")),
            children=(XmlElement("artifact", (("state", "available"), ("media-type", "text/plain"), ("filename", "a.txt"), ("handle", "art_1"))),),
        ),
        XmlElement("truncated", (("remaining", "1"), ("reason", "token-budget"), ("slot", "memory-context"))),
    )

    rendered = render_xml(node)
    assert '<field name="goal" type="string" purpose="goal"/>' in rendered
    assert '<value format="json" null="true"></value>' in rendered
    assert '<step handle="step_1" status="pending">do</step>' in rendered
    assert '<segment handle="seg_1" topic="t" recallable="true">s</segment>' in rendered
    assert '<memory handle="mem_1" kind="semantic" category="fact" instructional="false">m</memory>' in rendered
    assert '<artifact handle="art_1" filename="a.txt" media-type="text/plain" state="available"/>' in rendered
    assert '<truncated slot="memory-context" reason="token-budget" remaining="1"/>' in rendered


def test_xml_renderer_requires_complete_context_snapshot_root() -> None:
    with pytest.raises(ContextProtocolError, match="context-snapshot root required"):
        render_xml(XmlElement(tag="value", text="not a complete snapshot"))


def test_schema_resolution_precedes_duplicate_attribute_validation() -> None:
    node = snapshot(
        XmlElement(
            tag="user-supplied-tag",
            attributes=(("x", "1"), ("x", "2")),
        ),
    )
    with pytest.raises(ContextProtocolError, match="unregistered XML tag"):
        render_xml(node)


def test_root_validation_precedes_extension_specs_shape_validation() -> None:
    node = XmlElement(
        "context-snapshot",
        (*ROOT_ATTRIBUTES, ("protocol", "duplicate")),
    )

    with pytest.raises(ContextProtocolError, match="duplicate attribute"):
        render_xml(node, extension_specs=[])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "error"),
    [
        (XmlElement("field", (("name", "goal"), ("unknown", "x")), children=(XmlElement("value", text="ok"),)), "unknown attribute"),
        (XmlElement("field", (("name", "a"), ("name", "b")), children=(XmlElement("value", text="ok"),)), "duplicate attribute"),
        (XmlElement("field", children=(XmlElement("value", text="ok"),)), "missing required attribute"),
    ],
)
def test_xml_renderer_rejects_non_schema_attributes(field: XmlElement, error: str) -> None:
    node = snapshot(XmlElement("working-state", (("schema-version", "1"),), children=(field,)))
    with pytest.raises(ContextProtocolError, match=error):
        render_xml(node)


@pytest.mark.parametrize(
    "node",
    [
        snapshot(XmlElement("memory-context", children=(XmlElement("memory", (("handle", "m"), ("kind", "semantic"), ("instructional", "false")), text="x"),))),
        snapshot(XmlElement("artifact-catalog", (("scope", "session"), ("truncated", "false")), children=(XmlElement("artifact", (("handle", "a"), ("media-type", "text/plain"), ("state", "available"))),))),
    ],
)
def test_filename_and_memory_category_are_required(node: XmlElement) -> None:
    with pytest.raises(ContextProtocolError, match="missing required attribute"):
        render_xml(node)


@pytest.mark.parametrize("invalid", ["\x00", "\x01", "\x0b", "\ufffe", "\uffff", "\ud800", "\udfff"])
@pytest.mark.parametrize("location", ["text", "attribute"])
def test_xml_renderer_rejects_invalid_xml_1_characters(invalid: str, location: str) -> None:
    node = working_snapshot(invalid) if location == "text" else working_snapshot("ok", value_attributes=(("format", invalid),))
    assert_rejected_without_echo(node, "invalid XML character", invalid)


def test_xml_renderer_accepts_valid_controls_and_supplementary_characters() -> None:
    value = "tab\tline\nreturn\rface\U0001f600"
    assert value in render_xml(working_snapshot(value))


def test_attribute_controls_use_fixed_numeric_references() -> None:
    rendered = render_xml(working_snapshot("ok", value_attributes=(("format", "a\rb\nc\td"),)))
    assert 'format="a&#xD;b&#xA;c&#x9;d"' in rendered


def test_cdata_like_text_is_escaped_as_normal_text() -> None:
    rendered = render_xml(working_snapshot("<![CDATA[x]]>"))
    assert "&lt;![CDATA[x]]&gt;" in rendered
    assert "<![CDATA[" not in rendered


@pytest.mark.parametrize(
    ("node", "error"),
    [
        (snapshot(XmlElement("working-state", (("schema-version", "1"),), text="bad")), "text is not allowed"),
        (snapshot(XmlElement("working-state", (("schema-version", "1"),), children=(XmlElement("item", text="bad"),))), "child tag is not allowed"),
        (working_snapshot("ok").children[0].children[0].children[0], "context-snapshot root required"),
    ],
)
def test_xml_renderer_rejects_text_and_wrong_parent_paths(node: XmlElement, error: str) -> None:
    with pytest.raises(ContextProtocolError, match=error):
        render_xml(node)


def test_xml_renderer_rejects_text_with_children() -> None:
    node = snapshot(
        XmlElement(
            "working-state",
            (("schema-version", "1"),),
            text="bad",
            children=(XmlElement("field", (("name", "goal"),), children=(XmlElement("value", text="ok"),)),),
        ),
    )
    with pytest.raises(ContextProtocolError, match="text and children"):
        render_xml(node)


def test_empty_element_distinguishes_none_from_empty_text() -> None:
    node = snapshot(
        XmlElement("working-state", (("schema-version", "1"),), children=(
            XmlElement("field", (("name", "a"),), children=(XmlElement("value"),)),
            XmlElement("field", (("name", "b"),), children=(XmlElement("value", text=""),)),
        )),
    )
    rendered = render_xml(node)
    assert "<value/>" in rendered
    assert "<value></value>" in rendered
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")


def test_xml_tag_spec_and_core_schema_are_frozen_declarations() -> None:
    spec = XmlTagSpec()
    assert not hasattr(spec, "__dict__")
    with pytest.raises(FrozenInstanceError):
        spec.allow_text = True  # type: ignore[misc]
