"""Context Protocol v1 的安全、确定性 XML 序列化。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agentos.context.models import ContextProtocolError
from agentos.context.registry import ContextExtensionSpec
from agentos.context.xml_schema import CORE_XML_SCHEMA, XmlTagSpec

_EXTENSION_PATH = ("context-snapshot", "extensions", "extension")
_CORE_TAGS = frozenset(path[-1] for path in CORE_XML_SCHEMA)
_XML_NAME_PATTERN = re.compile(
    r"[:A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff"
    r"\u0370-\u037d\u037f-\u1fff\u200c-\u200d\u2070-\u218f"
    r"\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd"
    r"\U00010000-\U000EFFFF]"
    r"[:A-Z_a-z\-.0-9\u00b7\u00c0-\u00d6\u00d8-\u00f6"
    r"\u00f8-\u037d\u037f-\u1fff\u200c-\u200d\u203f-\u2040"
    r"\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf"
    r"\ufdf0-\ufffd\U00010000-\U000EFFFF]*",
)


@dataclass(frozen=True, slots=True)
class XmlElement:
    """调用方提供的不可变 XML 投影节点。"""

    tag: str
    attributes: tuple[tuple[str, str], ...] = ()
    text: str | None = None
    children: tuple[XmlElement, ...] = ()

    def __post_init__(self) -> None:
        if type(self.tag) is not str:
            raise ContextProtocolError("XML element tag must be a string")
        raw_attributes = _copy_sequence(self.attributes, "XML element attributes must be pairs")
        attributes: list[tuple[str, str]] = []
        for item in raw_attributes:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ContextProtocolError("XML element attributes must be pairs")
            if any(not isinstance(value, str) for value in item):
                raise ContextProtocolError(
                    "XML element attributes must be string pairs",
                )
            attributes.append((item[0], item[1]))
        children = _copy_sequence(self.children, "XML element children must be elements")
        if any(not isinstance(child, XmlElement) for child in children):
            raise ContextProtocolError("XML element children must be elements")
        if self.text is not None and not isinstance(self.text, str):
            raise ContextProtocolError("XML element text must be a string or None")
        object.__setattr__(self, "attributes", tuple(attributes))
        object.__setattr__(self, "children", children)


def _copy_sequence(value: object, message: str) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContextProtocolError(message)
    return tuple(value)


@dataclass(frozen=True, slots=True)
class _ValidatedNode:
    element: XmlElement
    spec: XmlTagSpec
    children: tuple[_ValidatedNode, ...]


def render_xml(
    node: XmlElement,
    *,
    extension_specs: Mapping[str, ContextExtensionSpec] | None = None,
) -> str:
    """校验并序列化一个完整 ContextSnapshot root。"""

    if not isinstance(node, XmlElement) or node.tag != "context-snapshot":
        raise ContextProtocolError("context-snapshot root required")
    root_spec = CORE_XML_SCHEMA[("context-snapshot",)]
    _validate_tag_spec(root_spec, delegated=False, extension=False)
    _validate_element(node, root_spec)
    _validate_child_names(node, root_spec)
    if extension_specs is None:
        specs: Mapping[str, ContextExtensionSpec] = {}
    elif not isinstance(extension_specs, Mapping):
        raise ContextProtocolError("extension_specs must be a mapping")
    else:
        specs = extension_specs
    validated = _validate_core(node, ("context-snapshot",), specs)
    return "".join(_serialize(validated, 0))


def _validate_core(
    node: XmlElement,
    path: tuple[str, ...],
    extension_specs: Mapping[str, ContextExtensionSpec],
) -> _ValidatedNode:
    spec = CORE_XML_SCHEMA.get(path)
    if spec is None:
        raise ContextProtocolError("unregistered XML tag")
    _validate_tag_spec(spec, delegated=path == _EXTENSION_PATH, extension=False)
    attributes = _validate_element(node, spec)
    if path == _EXTENSION_PATH:
        extension_spec = _select_extension(attributes, extension_specs)
        children = tuple(
            _validate_extension(child, (child.tag,), extension_spec)
            for child in node.children
        )
    else:
        _validate_child_names(node, spec)
        children = tuple(
            _validate_core(child, (*path, child.tag), extension_specs)
            for child in node.children
        )
    return _ValidatedNode(node, spec, children)


def _select_extension(
    attributes: Mapping[str, str], extension_specs: Mapping[str, ContextExtensionSpec]
) -> ContextExtensionSpec:
    namespace = attributes["namespace"]
    missing = object()
    spec = extension_specs.get(namespace, missing)  # type: ignore[arg-type]
    if spec is missing:
        raise ContextProtocolError("extension namespace is not registered")
    if not isinstance(spec, ContextExtensionSpec):
        raise ContextProtocolError("extension spec is invalid")
    if spec.namespace != namespace:
        raise ContextProtocolError("extension spec namespace mismatch")
    if attributes["version"] != spec.version:
        raise ContextProtocolError("extension version mismatch")
    for tag_spec in spec.tag_schemas.values():
        if not isinstance(tag_spec, XmlTagSpec):
            raise ContextProtocolError("extension XML tag spec is invalid")
        _validate_tag_spec(tag_spec, delegated=False, extension=True)
    return spec


def _validate_extension(
    node: XmlElement, path: tuple[str, ...], extension_spec: ContextExtensionSpec
) -> _ValidatedNode:
    spec = extension_spec.tag_schemas.get(path)
    if spec is None:
        raise ContextProtocolError("unregistered XML tag")
    _validate_tag_spec(spec, delegated=False, extension=True)
    _validate_element(node, spec)
    _validate_child_names(node, spec)
    children = tuple(
        _validate_extension(child, (*path, child.tag), extension_spec)
        for child in node.children
    )
    return _ValidatedNode(node, spec, children)


def _validate_tag_spec(
    spec: XmlTagSpec, *, delegated: bool, extension: bool
) -> None:
    tuples = (
        spec.required_attributes,
        spec.optional_attributes,
        spec.canonical_order,
    )
    if any(not isinstance(value, tuple) for value in tuples):
        raise ContextProtocolError("XML tag spec tuple is invalid")
    if spec.allowed_children is not None and not isinstance(
        spec.allowed_children,
        tuple,
    ):
        raise ContextProtocolError("XML tag spec tuple is invalid")
    if type(spec.allow_text) is not bool:
        raise ContextProtocolError("XML tag spec allow_text is invalid")
    if spec.allowed_children is None and (extension or not delegated):
        message = (
            "extension XML tag spec children are invalid"
            if extension
            else "core XML tag spec children are invalid"
        )
        raise ContextProtocolError(message)
    attribute_groups = (spec.required_attributes, spec.optional_attributes)
    for names in attribute_groups:
        if any(not isinstance(name, str) for name in names):
            raise ContextProtocolError("XML tag spec tuple is invalid")
        if len(names) != len(set(names)):
            raise ContextProtocolError("duplicate XML tag spec name")
    required = spec.required_attributes
    optional = spec.optional_attributes
    if set(required).intersection(optional):
        raise ContextProtocolError("overlapping XML tag spec attributes")
    if any(not isinstance(name, str) for name in spec.canonical_order):
        raise ContextProtocolError("XML tag spec tuple is invalid")
    if spec.canonical_order != required + optional:
        raise ContextProtocolError("canonical XML attribute order is invalid")
    children = spec.allowed_children or ()
    if any(not isinstance(name, str) for name in children):
        raise ContextProtocolError("XML tag spec tuple is invalid")
    if len(children) != len(set(children)):
        raise ContextProtocolError("duplicate XML tag spec name")
    for names in (*attribute_groups, spec.canonical_order, children):
        for name in names:
            if _XML_NAME_PATTERN.fullmatch(name) is None:
                raise ContextProtocolError("XML tag spec name is invalid")
            folded = name.casefold()
            if folded == "xmlns" or folded.startswith("xmlns:"):
                raise ContextProtocolError("reserved XML namespace name")


def _validate_element(node: XmlElement, spec: XmlTagSpec) -> dict[str, str]:
    names = tuple(name for name, _ in node.attributes)
    if len(names) != len(set(names)):
        raise ContextProtocolError("duplicate attribute")
    attributes = dict(node.attributes)
    if any(name not in attributes for name in spec.required_attributes):
        raise ContextProtocolError("missing required attribute")
    allowed = set(spec.required_attributes + spec.optional_attributes)
    if any(name not in allowed for name in attributes):
        raise ContextProtocolError("unknown attribute")
    for value in attributes.values():
        _validate_xml_characters(value)
    if node.text is not None:
        _validate_xml_characters(node.text)
    if node.text is not None and node.children:
        raise ContextProtocolError("XML element cannot contain text and children")
    if node.text is not None and not spec.allow_text:
        raise ContextProtocolError("text is not allowed")
    return attributes


def _validate_child_names(node: XmlElement, spec: XmlTagSpec) -> None:
    allowed = spec.allowed_children
    if allowed is None:
        return
    for child in node.children:
        if child.tag not in allowed:
            message = (
                "child tag is not allowed"
                if child.tag in _CORE_TAGS
                else "unregistered XML tag"
            )
            raise ContextProtocolError(message)


def _validate_xml_characters(value: str) -> None:
    for character in value:
        code = ord(character)
        if not (
            code in (0x9, 0xA, 0xD)
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or 0x10000 <= code <= 0x10FFFF
        ):
            raise ContextProtocolError("invalid XML character")


def _serialize(node: _ValidatedNode, level: int) -> list[str]:
    element = node.element
    prefix = "  " * level
    attributes = dict(element.attributes)
    rendered_attributes = "".join(
        f' {name}="{_escape_attribute(attributes[name])}"'
        for name in node.spec.canonical_order
        if name in attributes
    )
    if element.text is None and not node.children:
        return [f"{prefix}<{element.tag}{rendered_attributes}/>\n"]
    if element.text is not None:
        return [
            f"{prefix}<{element.tag}{rendered_attributes}>"
            f"{_escape_text(element.text)}</{element.tag}>\n",
        ]
    lines = [f"{prefix}<{element.tag}{rendered_attributes}>\n"]
    for child in node.children:
        lines.extend(_serialize(child, level + 1))
    lines.append(f"{prefix}</{element.tag}>\n")
    return lines


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attribute(value: str) -> str:
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
        "\r": "&#xD;",
        "\n": "&#xA;",
        "\t": "&#x9;",
    }
    return "".join(replacements.get(character, character) for character in value)
