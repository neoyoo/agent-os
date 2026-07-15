from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields
import importlib
import importlib.util
import pickle

import pytest

from agentos.artifacts import ArtifactRef
from agentos.messages import MessageStore, StoredMessage, ToolCall


def test_stored_message_is_frozen_and_normalizes_tuple_boundaries() -> None:
    artifact = ArtifactRef("art_1", "drawing.png", "image/png")
    tool_calls = [ToolCall("call_1", "read_file", {"path": "README.md"})]
    artifact_refs = [artifact]

    message = StoredMessage(
        id="msg_1",
        role="user",
        content="分析图纸",
        artifact_refs=artifact_refs,
        tool_calls=tool_calls,
    )
    artifact_refs.clear()
    tool_calls.clear()

    assert message.artifact_refs == (artifact,)
    assert len(message.tool_calls) == 1
    assert isinstance(message.artifact_refs, tuple)
    assert isinstance(message.tool_calls, tuple)
    with pytest.raises(FrozenInstanceError):
        message.content = "mutated"  # type: ignore[misc]


def test_tool_call_arguments_are_recursively_immutable_and_detached() -> None:
    arguments = {"filters": {"tags": ["a"]}}
    call = ToolCall("call_1", "search", arguments)
    arguments["filters"]["tags"].append("outside")  # type: ignore[index,union-attr]

    assert call.to_provider_dict() == {
        "id": "call_1",
        "name": "search",
        "arguments": {"filters": {"tags": ["a"]}},
    }
    with pytest.raises(TypeError):
        call.arguments["filters"]["tags"] += ("inside",)  # type: ignore[index,operator]


def test_artifact_ref_and_stored_message_exclude_runtime_provider_fields() -> None:
    assert [item.name for item in fields(ArtifactRef)] == [
        "artifact_id",
        "filename",
        "media_type",
    ]
    message_fields = {item.name for item in fields(StoredMessage)}

    assert message_fields == {
        "id",
        "role",
        "content",
        "artifact_refs",
        "tool_calls",
        "tool_call_id",
    }
    assert not message_fields & {
        "origin",
        "authority",
        "persistence",
        "visibility",
        "provider_file_id",
        "signed_url",
        "path",
        "base64",
    }
    assert not hasattr(StoredMessage, "to_provider_dict")


def test_legacy_message_name_and_migration_module_are_removed() -> None:
    messages = importlib.import_module("agentos.messages")

    assert not hasattr(messages, "Message")
    assert importlib.util.find_spec("agentos.messages._migration") is None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (True, 1),
        (False, 0),
        (1, 1.0),
    ],
)
def test_message_store_rejects_same_id_with_json_scalar_type_change(
    first: object,
    second: object,
) -> None:
    store = MessageStore()
    store.put(
        StoredMessage(
            id="msg_1",
            role="assistant",
            content="",
            tool_calls=(ToolCall("call_1", "inspect", {"value": first}),),
        ),
    )

    with pytest.raises(ValueError, match="message id conflict"):
        store.put(
            StoredMessage(
                id="msg_1",
                role="assistant",
                content="",
                tool_calls=(ToolCall("call_1", "inspect", {"value": second}),),
            ),
        )


def test_stored_message_and_tool_call_are_hashable_values() -> None:
    tool_call = ToolCall(
        "call_1",
        "inspect",
        {"filters": {"tags": ["phase2"]}},
    )
    message = StoredMessage(
        id="msg_1",
        role="assistant",
        content="",
        tool_calls=(tool_call,),
    )

    assert hash(tool_call) == hash(
        ToolCall(
            "call_1",
            "inspect",
            {"filters": {"tags": ["phase2"]}},
        ),
    )
    assert hash(message) == hash(
        StoredMessage(
            id="msg_1",
            role="assistant",
            content="",
            tool_calls=(tool_call,),
        ),
    )


def test_stored_message_supports_standard_value_copy_and_pickle() -> None:
    message = StoredMessage(
        id="msg_1",
        role="assistant",
        content="",
        tool_calls=(
            ToolCall(
                "call_1",
                "inspect",
                {"filters": {"tags": ["phase2"]}},
            ),
        ),
    )

    copied = deepcopy(message)
    restored = pickle.loads(pickle.dumps(message))
    projected = asdict(message)

    assert copied == message
    assert copied.tool_calls[0].arguments is message.tool_calls[0].arguments
    assert restored == message
    assert projected["tool_calls"][0]["arguments"] is message.tool_calls[0].arguments
