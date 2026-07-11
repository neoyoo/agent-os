from __future__ import annotations

import json

import pytest

from agentos.context.models import (
    ContextSensitiveDataError,
    ContextSlotProjection,
    ProjectionVariant,
)
from agentos.context.snapshot import (
    ContextSnapshotRenderer,
)
from agentos.context.sensitive import SensitiveRepresentationValidator
from agentos.context.xml import XmlElement


class ForbiddenTokenCounter:
    def count_text(self, text: str) -> int:
        raise AssertionError("unbudgeted rendering must not count tokens")


def memory_projection(
    text: str = "safe preview",
    *,
    handle: str = "mem_1",
    variants: tuple[ProjectionVariant, ...] = (),
) -> ContextSlotProjection:
    memory = XmlElement(
        "memory",
        (
            ("handle", handle),
            ("kind", "semantic"),
            ("category", "fact"),
            ("instructional", "false"),
        ),
        text=text,
    )
    full = ProjectionVariant(XmlElement("memory-context", children=(memory,)))
    return ContextSlotProjection(
        slot="memory-context",
        owner="MemoryRuntime",
        variants=(full, *variants),
    )


@pytest.mark.parametrize(
    ("category", "value"),
    [
        ("data-url", "prefix data:image/png;BASE64,AAAA"),
        ("provider-file-id", "body file-ABCDEFGHIJKLMNOPQRST suffix"),
        ("provider-file-id", "body file_0123456789abcdefghij suffix"),
        ("absolute-path", r"C:\Users\runner\secret.txt"),
        ("absolute-path", "C:/Users/runner/secret.txt"),
        ("absolute-path", r"\\server\share\secret.txt"),
        ("absolute-path", "/var/tmp/agentos/secret.txt"),
    ],
)
def test_validator_rejects_sensitive_scalar_without_echo(
    category: str,
    value: str,
) -> None:
    with pytest.raises(ContextSensitiveDataError) as error:
        SensitiveRepresentationValidator().validate(
            {"outer": ["safe", {"inner": value}]},
            slot="memory-context",
        )
    assert str(error.value) == (
        f"sensitive representation category={category} slot=memory-context"
    )
    assert value not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://storage.example/item?X-Amz-Signature=secret",
        "https://storage.example/item?X-Amz-Signature=",
        "https://storage.example/item?x-goog-signature=secret",
        "https://storage.example/item?signature=secret&googleaccessid=user",
        "https://storage.example/item?sig=secret&sv=2024-01-01&se=tomorrow",
        "https://storage.example/item?sig=secret&sv=2024-01-01&sp=read",
        "https://storage.example/item?%78-amz-signature=secret",
    ],
)
def test_renderer_rejects_signed_url_embedded_in_body(url: str) -> None:
    source = f"download preview at {url} before expiry"
    with pytest.raises(ContextSensitiveDataError) as error:
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (memory_projection(source),),
        )
    assert str(error.value) == (
        "sensitive representation category=signed-url slot=memory-context"
    )
    assert source not in str(error.value)


def test_renderer_checks_xml_attributes_before_escaping() -> None:
    source = 'C:\\private\\a&b"secret.txt'
    with pytest.raises(ContextSensitiveDataError) as error:
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (memory_projection(handle=source),),
        )
    assert str(error.value) == (
        "sensitive representation category=absolute-path slot=memory-context"
    )
    assert source not in str(error.value)


def test_renderer_checks_nested_json_text_before_escaping() -> None:
    source = "data:application/octet-stream;base64,QUJDRA=="
    body = json.dumps({"items": [{"preview": source}]})
    with pytest.raises(ContextSensitiveDataError) as error:
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (memory_projection(body),),
        )
    assert str(error.value) == (
        "sensitive representation category=data-url slot=memory-context"
    )
    assert source not in str(error.value)


def test_renderer_validates_every_complete_variant() -> None:
    compact = ProjectionVariant(
        XmlElement(
            "memory-context",
            children=(
                XmlElement(
                    "memory",
                    (
                        ("handle", "mem_2"),
                        ("kind", "semantic"),
                        ("category", "fact"),
                        ("instructional", "false"),
                    ),
                    text="file-ABCDEFGHIJKLMNOPQRST",
                ),
            ),
        ),
        omitted_count=1,
    )
    with pytest.raises(ContextSensitiveDataError):
        ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
            (memory_projection(variants=(compact,)),),
        )


@pytest.mark.parametrize(
    "allowed",
    [
        "the word base64 is ordinary text",
        "file_name file_1 file-short",
        "/",
        "relative/path/to/file.txt",
        r"relative\path\to\file.txt",
        "notes.txt",
        "https://example.com/a/b/c",
        "https://example.com/item?sig=small",
        "https://example.com/item?sig=small&sv=1",
        "https://example.com/item?signature=small",
        "https://example.com/item?googleaccessid=user",
    ],
)
def test_renderer_allows_non_sensitive_lookalikes(allowed: str) -> None:
    snapshot = ContextSnapshotRenderer(ForbiddenTokenCounter()).render(
        (memory_projection(allowed),),
    )
    assert "<memory-context>" in snapshot.xml
