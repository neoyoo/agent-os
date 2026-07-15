from dataclasses import FrozenInstanceError
from typing import get_args, get_type_hints

import pytest

from agentos._internal_transcript import InternalTranscriptValue
from agentos.context import ContextSnapshot
from agentos.providers import (
    FilePart,
    ImagePart,
    InputAuthority,
    InputOrigin,
    PersistencePolicy,
    ProviderBinaryPayload,
    ProviderFunctionSpec,
    ProviderInputItem,
    ProviderInputKind,
    ProviderRequest,
    ProviderResponse,
    ProviderRole,
    ProviderToolCall,
    ProviderToolSpec,
    TextPart,
    VisibilityPolicy,
)


def _raw_provider_input(**overrides: object) -> ProviderInputItem:
    values: dict[str, object] = {
        "role": "user",
        "kind": "business_message",
        "origin": "message_store",
        "authority": "conversation_data",
        "persistence": "stored",
        "visibility": "conversation",
        "content": (TextPart("hello"),),
    }
    values.update(overrides)
    return ProviderInputItem(**values)  # type: ignore[arg-type]


def _binary_payload(media_type: str = "image/png") -> ProviderBinaryPayload:
    return ProviderBinaryPayload(
        handle="art_1",
        media_type=media_type,
        data=b"content",
    )


def test_provider_binary_payload_is_the_public_content_boundary() -> None:
    payload = ProviderBinaryPayload(
        handle="art_1",
        media_type="image/png",
        data=b"image-bytes",
        filename="diagram.png",
    )

    assert get_type_hints(ImagePart)["payload"] is ProviderBinaryPayload
    assert get_type_hints(FilePart)["payload"] is ProviderBinaryPayload
    assert "image-bytes" not in repr(payload)
    with pytest.raises(FrozenInstanceError):
        payload.handle = "art_2"  # type: ignore[misc]


@pytest.mark.parametrize("part_type", [ImagePart, FilePart])
def test_binary_content_parts_reject_non_payload_values(part_type: object) -> None:
    with pytest.raises(TypeError, match="ProviderBinaryPayload"):
        part_type(object())  # type: ignore[operator]


def test_provider_binary_payload_rejects_mutable_or_external_sources() -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        ProviderBinaryPayload(
            handle="art_1",
            media_type="image/png",
            data=bytearray(b"mutable"),  # type: ignore[arg-type]
        )


def test_provider_input_literal_sets_are_closed() -> None:
    assert set(get_args(ProviderRole)) == {"user", "assistant", "tool"}
    assert set(get_args(ProviderInputKind)) == {
        "context_snapshot",
        "business_message",
        "tool_result",
        "recalled_message",
        "model_task",
        "context_mount",
    }
    assert set(get_args(InputOrigin)) == {
        "runtime",
        "message_store",
        "recall_runtime",
        "artifact_runtime",
    }
    assert set(get_args(InputAuthority)) == {
        "context_data",
        "conversation_data",
        "tool_data",
        "artifact_data",
    }
    assert set(get_args(PersistencePolicy)) == {"stored", "ephemeral"}
    assert set(get_args(VisibilityPolicy)) == {"conversation", "internal"}


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            lambda: ProviderInputItem.context_snapshot("<context-snapshot/>\n"),
            (
                "user",
                "context_snapshot",
                "runtime",
                "context_data",
                "ephemeral",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.business_user("hello"),
            (
                "user",
                "business_message",
                "message_store",
                "conversation_data",
                "stored",
                "conversation",
            ),
        ),
        (
            lambda: ProviderInputItem.business_assistant("hello"),
            (
                "assistant",
                "business_message",
                "message_store",
                "conversation_data",
                "stored",
                "conversation",
            ),
        ),
        (
            lambda: ProviderInputItem.tool_result("call_1", "ok"),
            (
                "tool",
                "tool_result",
                "message_store",
                "tool_data",
                "stored",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.recalled_user("old"),
            (
                "user",
                "recalled_message",
                "recall_runtime",
                "conversation_data",
                "ephemeral",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.recalled_assistant("old"),
            (
                "assistant",
                "recalled_message",
                "recall_runtime",
                "conversation_data",
                "ephemeral",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.recalled_tool("call_1", "old"),
            (
                "tool",
                "recalled_message",
                "recall_runtime",
                "tool_data",
                "ephemeral",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.model_task("summarize this transcript"),
            (
                "user",
                "model_task",
                "runtime",
                "context_data",
                "ephemeral",
                "internal",
            ),
        ),
        (
            lambda: ProviderInputItem.context_mount((ImagePart(_binary_payload()),)),
            (
                "user",
                "context_mount",
                "artifact_runtime",
                "artifact_data",
                "ephemeral",
                "internal",
            ),
        ),
    ],
)
def test_provider_input_factories_freeze_the_metadata_matrix(
    factory: object,
    expected: tuple[str, str, str, str, str, str],
) -> None:
    item = factory()  # type: ignore[operator]

    assert (
        item.role,
        item.kind,
        item.origin,
        item.authority,
        item.persistence,
        item.visibility,
    ) == expected


def test_context_snapshot_item_has_sdk_fixed_metadata_and_content() -> None:
    item = ProviderInputItem.context_snapshot("<context-snapshot/>\n")

    assert item.content == (TextPart("<context-snapshot/>\n"),)
    assert item.tool_calls == ()
    assert item.tool_call_id is None


def test_model_task_item_has_factory_only_text_content() -> None:
    item = ProviderInputItem.model_task("untrusted task data")

    assert item.content == (TextPart("untrusted task data"),)
    assert item.tool_calls == ()
    assert item.tool_call_id is None
    with pytest.raises(TypeError, match="text"):
        ProviderInputItem.model_task(object())  # type: ignore[arg-type]


def test_model_task_rejects_direct_public_field_construction() -> None:
    with pytest.raises(ValueError, match=r"model_task\(\) factory"):
        _raw_provider_input(
            kind="model_task",
            origin="runtime",
            authority="context_data",
            persistence="ephemeral",
            visibility="internal",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "assistant", "kind": "context_snapshot"},
        {"kind": "tool_result", "tool_call_id": None},
        {"kind": "business_message", "origin": "recall_runtime"},
        {"kind": "recalled_message", "persistence": "stored"},
        {"kind": "context_mount", "authority": "conversation_data"},
        {"kind": "unknown"},
    ],
)
def test_provider_input_rejects_cross_matrix_combinations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="metadata matrix"):
        _raw_provider_input(**overrides)


def test_provider_input_requires_tool_call_id_only_for_tool_data() -> None:
    with pytest.raises(ValueError, match="tool_call_id"):
        ProviderInputItem.tool_result("", "missing")
    with pytest.raises(ValueError, match="tool_call_id"):
        _raw_provider_input(tool_call_id="call_1")


def test_provider_input_and_request_are_deeply_immutable() -> None:
    arguments = {"filters": {"tags": ["phase2"]}}
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }
    tool_call = ProviderToolCall("call_1", "search", arguments)
    tool_spec = ProviderToolSpec(
        function=ProviderFunctionSpec(
            name="search",
            description="Search.",
            parameters=parameters,
        ),
    )
    messages = [ProviderInputItem.business_assistant("", (tool_call,))]
    tools = [tool_spec]

    request = ProviderRequest(system="system", messages=messages, tools=tools)
    arguments["filters"]["tags"].append("mutated")  # type: ignore[index,union-attr]
    parameters["properties"]["other"] = {}  # type: ignore[index]
    messages.clear()
    tools.clear()

    assert isinstance(request.messages, tuple)
    assert isinstance(request.tools, tuple)
    assert request.messages[0].tool_calls[0].arguments["filters"]["tags"] == (
        "phase2",
    )
    assert "other" not in request.tools[0].function.parameters["properties"]
    with pytest.raises(FrozenInstanceError):
        request.system = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        request.messages[0].tool_calls[0].arguments["filters"]["tags"] += (  # type: ignore[index,operator]
            "mutated",
        )
    with pytest.raises(TypeError):
        request.tools[0].function.parameters["properties"]["query"] = {}  # type: ignore[index]


def test_provider_request_rejects_legacy_dict_messages() -> None:
    with pytest.raises(TypeError, match="ProviderInputItem"):
        ProviderRequest(
            system="system",
            messages=({"role": "user", "content": "hello"},),  # type: ignore[arg-type]
        )


def test_provider_request_accepts_only_an_isolated_model_task_plane() -> None:
    task = ProviderInputItem.model_task("conversation data")

    request = ProviderRequest(system="trusted task instruction", messages=(task,))

    assert request.messages == (task,)
    assert request.tools == ()
    assert request.parallel_tool_calls is None


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {
            "messages": (
                ProviderInputItem.model_task("task data"),
                ProviderInputItem.business_user("turn data"),
            ),
        },
        {
            "messages": (
                ProviderInputItem.model_task("first"),
                ProviderInputItem.model_task("second"),
            ),
        },
        {
            "messages": (ProviderInputItem.model_task("task data"),),
            "tools": (
                ProviderToolSpec(
                    function=ProviderFunctionSpec(
                        name="lookup",
                        description="Lookup data.",
                        parameters={"type": "object", "properties": {}},
                    ),
                ),
            ),
        },
        {
            "messages": (ProviderInputItem.model_task("task data"),),
            "parallel_tool_calls": True,
        },
    ],
)
def test_provider_request_rejects_mixed_or_tool_enabled_model_task_plane(
    request_kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="model_task request"):
        ProviderRequest(
            system="trusted task instruction",
            **request_kwargs,  # type: ignore[arg-type]
        )


def test_internal_provider_transcript_values_share_nominal_marker() -> None:
    values = (
        ContextSnapshot(xml="<context-snapshot/>\n"),
        ProviderInputItem.business_user("hello"),
        ProviderRequest(system="system", messages=(), tools=()),
        ProviderResponse(content="internal"),
    )

    assert all(isinstance(value, InternalTranscriptValue) for value in values)
    assert InternalTranscriptValue.__slots__ == ()
    with pytest.raises(AttributeError):
        InternalTranscriptValue().state = "forbidden"  # type: ignore[attr-defined]


def test_provider_content_rejects_unknown_parts() -> None:
    with pytest.raises(TypeError, match="content parts"):
        _raw_provider_input(content=(object(),))
    with pytest.raises(TypeError, match="content parts"):
        ProviderInputItem.context_mount(
            (FilePart(_binary_payload("application/pdf")), object()),  # type: ignore[arg-type]
        )
