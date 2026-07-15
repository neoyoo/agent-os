from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from agentos.providers import (
    FilePart,
    ImagePart,
    ProviderInputItem,
    ProviderRequest,
    TextPart,
)
from agentos.providers.anthropic_wire import build_anthropic_messages
from agentos.providers.openai_chat_wire import build_openai_chat_payload
from agentos.providers.openai_responses_wire import build_openai_responses_payload
from tests._provider_binary import binary_payload


PROVIDERS = Path(__file__).parents[2] / "src" / "agentos" / "providers"


def _binary_request() -> ProviderRequest:
    image = binary_payload(
        handle="art_image",
        filename="drawing.png",
        media_type="image/png",
        data=b"image-bytes",
    )
    return ProviderRequest(
        system="system",
        messages=(
            ProviderInputItem.context_mount(
                (TextPart("mounted"), ImagePart(image, detail="high")),
            ),
        ),
    )


@pytest.mark.parametrize("wire", ["responses", "chat", "anthropic"])
def test_provider_wire_encoding_does_not_mutate_binary_request(wire: str) -> None:
    request = _binary_request()
    original = deepcopy(request)

    if wire == "responses":
        build_openai_responses_payload(model="model", request=request)
    elif wire == "chat":
        build_openai_chat_payload(
            model="model",
            request=request,
            include_parallel_tool_calls=True,
        )
    else:
        build_anthropic_messages(request.messages)

    assert request == original
    image = request.messages[0].content[1]
    assert isinstance(image, ImagePart)
    assert image.payload.data == b"image-bytes"


def test_responses_and_anthropic_file_encoding_preserves_payload() -> None:
    document = binary_payload(
        handle="art_pdf",
        filename="drawing.pdf",
        media_type="application/pdf",
        data=b"pdf-bytes",
    )
    request = ProviderRequest(
        system="system",
        messages=(
            ProviderInputItem.context_mount((FilePart(document),)),
        ),
    )
    original = deepcopy(request)

    build_openai_responses_payload(model="model", request=request)
    build_anthropic_messages(request.messages)

    assert request == original
    assert document.data == b"pdf-bytes"


def test_provider_modules_do_not_depend_on_attachment_sources() -> None:
    forbidden = (
        "agentos" + ".attachments",
        "ProviderFile" + "Source",
        "LocalFile" + "Source",
        "InlineBase64" + "Source",
        "Url" + "Source",
    )

    for source in PROVIDERS.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert not any(name in text for name in forbidden), source.name
