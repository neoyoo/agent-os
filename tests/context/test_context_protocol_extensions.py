from collections.abc import Iterator, Mapping

import pytest

from agentos.context.models import ContextProtocolError
from agentos.context.registry import ContextExtensionSpec
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
    return XmlElement("context-snapshot", ROOT_ATTRIBUTES, children=children)


def working_snapshot() -> XmlElement:
    return snapshot(XmlElement("working-state", (("schema-version", "1"),)))


def extension_spec(
    namespace: str,
    *,
    version: str = "1.0",
    tag_schemas: dict[tuple[str, ...], object] | None = None,
) -> ContextExtensionSpec:
    return ContextExtensionSpec(
        namespace=namespace,
        owner="ExtensionRuntime",
        version=version,
        tag_schemas=tag_schemas
        or {
            ("approval",): XmlTagSpec(
                required_attributes=("status",),
                canonical_order=("status",),
                allow_text=True,
            ),
        },
        max_tokens=512,
        trim_rank=5,
    )


def extension_snapshot(
    namespace: str = "com.example.hitl",
    *,
    version: str = "1.0",
    payload: XmlElement | None = None,
) -> XmlElement:
    payload = payload or XmlElement(
        "approval",
        (("status", "pending"),),
        text="review",
    )
    return snapshot(
        XmlElement(
            "extensions",
            children=(
                XmlElement(
                    "extension",
                    (("namespace", namespace), ("version", version)),
                    children=(payload,),
                ),
            ),
        ),
    )


def assert_rejected_without_echo(
    node: XmlElement,
    expected: str,
    sensitive: str,
    *,
    extension_specs: object,
) -> None:
    with pytest.raises(ContextProtocolError, match=expected) as error:
        render_xml(node, extension_specs=extension_specs)  # type: ignore[arg-type]
    assert sensitive not in str(error.value)


def test_extensions_select_schema_by_namespace_with_reusable_relative_paths() -> None:
    specs = {
        "com.example.hitl": extension_spec("com.example.hitl"),
        "com.example.review": extension_spec(
            "com.example.review",
            version="2.0",
            tag_schemas={
                ("approval",): XmlTagSpec(
                    required_attributes=("decision",),
                    canonical_order=("decision",),
                    allow_text=True,
                ),
            },
        ),
    }
    node = snapshot(
        XmlElement(
            "extensions",
            children=(
                XmlElement(
                    "extension",
                    (("version", "1.0"), ("namespace", "com.example.hitl")),
                    children=(
                        XmlElement(
                            "approval",
                            (("status", "pending"),),
                            text="one",
                        ),
                    ),
                ),
                XmlElement(
                    "extension",
                    (("namespace", "com.example.review"), ("version", "2.0")),
                    children=(
                        XmlElement(
                            "approval",
                            (("decision", "accept"),),
                            text="two",
                        ),
                    ),
                ),
            ),
        ),
    )
    rendered = render_xml(node, extension_specs=specs)
    assert '<approval status="pending">one</approval>' in rendered
    assert '<approval decision="accept">two</approval>' in rendered


def test_extension_relative_paths_support_nested_children() -> None:
    spec = extension_spec(
        "com.example.hitl",
        tag_schemas={
            ("approval",): XmlTagSpec(allowed_children=("reason",)),
            ("approval", "reason"): XmlTagSpec(allow_text=True),
        },
    )
    payload = XmlElement("approval", children=(XmlElement("reason", text="because"),))
    rendered = render_xml(
        extension_snapshot(payload=payload),
        extension_specs={spec.namespace: spec},
    )
    assert "<reason>because</reason>" in rendered


@pytest.mark.parametrize(
    ("node", "specs", "error", "sensitive"),
    [
        (extension_snapshot("com.secret.unknown"), {}, "extension namespace is not registered", "com.secret.unknown"),
        (extension_snapshot(version="2.0"), {"com.example.hitl": extension_spec("com.example.hitl")}, "extension version mismatch", "2.0"),
        (extension_snapshot(payload=XmlElement("unknown", text="secret")), {"com.example.hitl": extension_spec("com.example.hitl")}, "unregistered XML tag", "secret"),
        (extension_snapshot(payload=XmlElement("approval", (("unknown", "secret"), ("status", "pending")), text="ok")), {"com.example.hitl": extension_spec("com.example.hitl")}, "unknown attribute", "secret"),
        (extension_snapshot(), {"com.example.hitl": object()}, "extension spec is invalid", "com.example.hitl"),
        (extension_snapshot(), {"com.example.hitl": None}, "extension spec is invalid", "com.example.hitl"),
        (extension_snapshot(), {"com.example.hitl": extension_spec("com.example.other")}, "extension spec namespace mismatch", "com.example.hitl"),
    ],
)
def test_extension_errors_are_stable_and_do_not_echo_untrusted_values(
    node: XmlElement,
    specs: object,
    error: str,
    sensitive: str,
) -> None:
    assert_rejected_without_echo(
        node,
        error,
        sensitive,
        extension_specs=specs,
    )


def test_extension_wrapper_core_validation_precedes_namespace_selection() -> None:
    node = snapshot(
        XmlElement(
            "extensions",
            children=(
                XmlElement(
                    "extension",
                    (
                        ("namespace", "com.secret.unknown"),
                        ("namespace", "com.secret.unknown"),
                        ("version", "1.0"),
                    ),
                    children=(
                        XmlElement(
                            "approval",
                            (("status", "pending"),),
                            text="ok",
                        ),
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ContextProtocolError, match="duplicate attribute"):
        render_xml(node, extension_specs={})


@pytest.mark.parametrize(
    "node",
    [
        snapshot(XmlElement("working-state", (("schema-version", "1"),) * 2)),
        snapshot(
            XmlElement(
                "extensions",
                children=(
                    XmlElement(
                        "extension",
                        (("namespace", "com.example.hitl"),) * 2
                        + (("version", "1.0"),),
                    ),
                ),
            ),
        ),
    ],
)
def test_node_validation_precedes_unrelated_invalid_extension_entry(
    node: XmlElement,
) -> None:
    specs = {"com.example.unrelated": object()}
    with pytest.raises(ContextProtocolError, match="duplicate attribute"):
        render_xml(node, extension_specs=specs)  # type: ignore[arg-type]


def test_changing_extension_mapping_uses_stable_error_without_namespace() -> None:
    namespace = "com.secret.changing"

    class ChangingMapping(Mapping[str, ContextExtensionSpec]):
        def __contains__(self, key: object) -> bool:
            return key == namespace

        def __getitem__(self, key: str) -> ContextExtensionSpec:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

    with pytest.raises(ContextProtocolError) as error:
        render_xml(
            extension_snapshot(namespace),
            extension_specs=ChangingMapping(),
        )
    assert str(error.value) == "extension namespace is not registered"
    assert namespace not in str(error.value)


@pytest.mark.parametrize(
    ("schema", "error"),
    [
        (object(), "extension XML tag spec is invalid"),
        (XmlTagSpec(required_attributes="status", canonical_order=("status",)), "XML tag spec tuple is invalid"),
        (XmlTagSpec(required_attributes=("status", "status"), canonical_order=("status", "status")), "duplicate XML tag spec name"),
        (XmlTagSpec(required_attributes=("status",), optional_attributes=("status",), canonical_order=("status", "status")), "overlapping XML tag spec attributes"),
        (XmlTagSpec(required_attributes=("status",), canonical_order=()), "canonical XML attribute order is invalid"),
        (XmlTagSpec(required_attributes=("bad name",), canonical_order=("bad name",)), "XML tag spec name is invalid"),
        (XmlTagSpec(required_attributes=("xmlns",), canonical_order=("xmlns",)), "reserved XML namespace name"),
        (XmlTagSpec(required_attributes=("xmlns:x",), canonical_order=("xmlns:x",)), "reserved XML namespace name"),
        (XmlTagSpec(allow_text=1), "XML tag spec allow_text is invalid"),
        (XmlTagSpec(allowed_children=None), "extension XML tag spec children are invalid"),
        (XmlTagSpec(allowed_children=("child", "child")), "duplicate XML tag spec name"),
    ],
)
def test_extension_xml_tag_spec_shape_is_strict(schema: object, error: str) -> None:
    spec = extension_spec("com.example.hitl", tag_schemas={("approval",): schema})
    with pytest.raises(ContextProtocolError, match=error):
        render_xml(extension_snapshot(), extension_specs={spec.namespace: spec})


@pytest.mark.parametrize("extension_specs", [[], "specs", 42])
def test_render_xml_requires_extension_specs_mapping(extension_specs: object) -> None:
    with pytest.raises(ContextProtocolError, match="extension_specs must be a mapping"):
        render_xml(working_snapshot(), extension_specs=extension_specs)  # type: ignore[arg-type]
