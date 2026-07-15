import ast
import inspect
import json
from pathlib import Path

import pytest

from agentos.artifacts import ArtifactRef
from agentos.persistence import (
    DurableSessionStore,
    HotSessionState,
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
    HotSessionStore,
    RedisHotSessionStore,
)
from agentos.persistence.session_serializers import (
    message_from_dict,
    message_to_dict,
    tool_call_to_dict,
)
from agentos.messages import MessageRef, StoredMessage, ToolCall


def _annotation_text(annotation: object) -> str:
    if isinstance(annotation, str):
        return annotation
    return str(annotation)


@pytest.mark.parametrize(
    ("owner", "member", "parameter"),
    [
        (HotSessionStore, "append_hot_message", "message"),
        (HotSessionStore, "get_hot_messages", "return"),
        (DurableSessionStore, "append_message", "message"),
        (DurableSessionStore, "get_messages", "return"),
        (InMemoryHotSessionStore, "append_hot_message", "message"),
        (InMemoryHotSessionStore, "get_hot_messages", "return"),
        (InMemoryDurableSessionStore, "append_message", "message"),
        (InMemoryDurableSessionStore, "get_messages", "return"),
        (RedisHotSessionStore, "append_hot_message", "message"),
        (RedisHotSessionStore, "get_hot_messages", "return"),
    ],
)
def test_memory_public_message_annotations_use_stored_message(
    owner: type[object],
    member: str,
    parameter: str,
) -> None:
    signature = inspect.signature(getattr(owner, member))
    annotation = (
        signature.return_annotation
        if parameter == "return"
        else signature.parameters[parameter].annotation
    )

    assert "StoredMessage" in _annotation_text(annotation)


@pytest.mark.parametrize(
    ("function", "parameter"),
    [
        (message_to_dict, "message"),
        (message_from_dict, "return"),
    ],
)
def test_session_serializer_annotations_use_stored_message(
    function: object,
    parameter: str,
) -> None:
    signature = inspect.signature(function)
    annotation = (
        signature.return_annotation
        if parameter == "return"
        else signature.parameters[parameter].annotation
    )

    assert "StoredMessage" in _annotation_text(annotation)


def test_hot_session_state_annotations_use_stored_message() -> None:
    field_annotation = HotSessionState.__annotations__["recent_messages"]
    init_annotation = inspect.signature(HotSessionState).parameters[
        "recent_messages"
    ].annotation

    assert "StoredMessage" in _annotation_text(field_annotation)
    assert "StoredMessage" in _annotation_text(init_annotation)


def test_session_sources_do_not_import_legacy_message_name() -> None:
    modules = (
        "agentos.persistence.in_memory_session",
        "agentos.persistence.redis_session",
        "agentos.persistence.session_serializers",
        "agentos.persistence.session_store",
    )
    legacy_imports: list[str] = []
    for module_name in modules:
        module = __import__(module_name, fromlist=["__name__"])
        module_path = Path(inspect.getfile(module))
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None or not node.module.startswith("agentos.messages"):
                continue
            if any(alias.name == "Message" for alias in node.names):
                legacy_imports.append(module_name)

    assert legacy_imports == []


def test_hot_session_state_freezes_collections() -> None:
    message = StoredMessage(id="msg_1", role="user", content="hello")
    state = HotSessionState(
        session_id="session_1",
        active_refs=[MessageRef("msg_1")],
        recent_messages=[message],
        temporary_recalled_refs=["msg_2"],
        segment_refs={"seg_1": ["msg_1"]},
    )

    assert state.active_refs == (MessageRef("msg_1"),)
    assert state.recent_messages == (message,)
    assert state.temporary_recalled_refs == ("msg_2",)
    assert state.segment_refs == {"seg_1": ("msg_1",)}


def test_memory_tool_call_serializer_thaws_nested_arguments() -> None:
    encoded = tool_call_to_dict(
        ToolCall(
            id="call_1",
            name="inspect",
            arguments={"filters": {"tags": ["phase2"]}},
        ),
    )

    assert json.loads(json.dumps(encoded)) == {
        "id": "call_1",
        "name": "inspect",
        "arguments": {"filters": {"tags": ["phase2"]}},
    }


def test_memory_message_serializer_round_trip_returns_stored_message() -> None:
    artifact = ArtifactRef(
        artifact_id="art_1",
        filename="report.json",
        media_type="application/json",
    )
    message = StoredMessage(
        id="msg_1",
        role="assistant",
        content="done",
        artifact_refs=(artifact,),
        tool_calls=(
            ToolCall(
                id="call_1",
                name="inspect",
                arguments={"filters": {"tags": ["phase2"]}},
            ),
        ),
    )

    encoded = message_to_dict(message)
    restored = message_from_dict(encoded)

    assert encoded["artifact_refs"] == [
        {
            "artifact_id": "art_1",
            "filename": "report.json",
            "media_type": "application/json",
        },
    ]
    assert type(restored) is StoredMessage
    assert restored == message


def test_memory_message_deserializer_defaults_missing_artifact_refs_to_empty() -> None:
    restored = message_from_dict(
        {
            "id": "msg_legacy",
            "role": "user",
            "content": "hello",
            "tool_calls": [],
            "tool_call_id": None,
        },
    )

    assert restored.artifact_refs == ()
