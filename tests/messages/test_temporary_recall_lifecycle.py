import inspect

import pytest

from agentos.messages import ActiveWindow, MessageRef, MessageRuntime, StoredMessage
from agentos.messages import runtime as runtime_module


def _hydrate_temporary(runtime: MessageRuntime, message_id: str) -> StoredMessage:
    message = StoredMessage(id=message_id, role="user", content=message_id)
    runtime.hydrate_messages([message])
    runtime.active_window.prepend_temporary([message.id])
    return message


def test_materialize_active_does_not_consume_temporary_refs() -> None:
    runtime = MessageRuntime()
    recalled = _hydrate_temporary(runtime, "msg_99")

    assert runtime.materialize_active() == [recalled]
    assert runtime.materialize_active() == [recalled]

    runtime.consume_temporary_refs((recalled.id,))

    assert runtime.materialize_active() == []


def test_consumption_does_not_clear_temporary_refs_added_after_snapshot() -> None:
    runtime = MessageRuntime()
    first = _hydrate_temporary(runtime, "msg_1")
    snapshot = runtime.snapshot_active_with_refs()
    receipt_ids = tuple(ref.message_id for ref, _ in snapshot if ref.temporary)
    second = _hydrate_temporary(runtime, "msg_2")

    runtime.consume_temporary_refs(receipt_ids)

    assert receipt_ids == (first.id,)
    assert runtime.materialize_active() == [second]


def test_active_window_enforces_unique_message_ids_at_every_entry() -> None:
    window = ActiveWindow()
    window.append("msg_1", temporary=True)

    window.append("msg_1", temporary=True)

    assert window.snapshot_refs() == (MessageRef("msg_1", temporary=True),)
    assert isinstance(window.refs, tuple)
    with pytest.raises(ValueError, match="duplicate active message ref: msg_1"):
        ActiveWindow.from_refs(
            [
                MessageRef("msg_1"),
                MessageRef("msg_1", temporary=True),
            ],
        )


def test_provider_projection_does_not_consume_temporary_refs() -> None:
    runtime = MessageRuntime()
    recalled = _hydrate_temporary(runtime, "msg_1")

    first = runtime.materialize_provider_messages()
    second = runtime.materialize_provider_messages()

    assert [message.content for message in first] == [recalled.content]
    assert [message.content for message in second] == [recalled.content]
    assert runtime.has_temporary_recalled()


def test_message_runtime_does_not_import_provider_types_directly() -> None:
    source = inspect.getsource(runtime_module)

    assert "from agentos.providers import" not in source
