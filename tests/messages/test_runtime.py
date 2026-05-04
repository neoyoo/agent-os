from agentos.messages import MessageRuntime, ToolCall, ToolPairWindowError


def test_message_runtime_appends_original_messages_and_active_refs() -> None:
    runtime = MessageRuntime()

    user = runtime.append_user("Build Phase 1.")
    assistant = runtime.append_assistant("Working on it.")

    assert runtime.store.get(user.id).content == "Build Phase 1."
    assert runtime.store.get(assistant.id).content == "Working on it."
    assert [message.id for message in runtime.materialize_active()] == [
        user.id,
        assistant.id,
    ]


def test_active_messages_materialize_provider_shape() -> None:
    runtime = MessageRuntime()
    runtime.append_user("Hello")
    runtime.append_assistant("Hi")

    assert runtime.materialize_provider_messages() == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_tool_call_provider_dict_deep_copies_arguments() -> None:
    nested = {"path": {"value": "pyproject.toml"}}
    tool_call = ToolCall(id="call_1", name="read_file", arguments=nested)

    provider_dict = tool_call.to_provider_dict()
    nested["path"]["value"] = "mutated"  # type: ignore[index]

    assert provider_dict["arguments"] == {"path": {"value": "pyproject.toml"}}


def test_temporary_recalled_refs_are_deduplicated_before_next_request() -> None:
    runtime = MessageRuntime()
    first = runtime.append_user("Original detail")
    runtime.append_user("Current task")
    runtime.active_window.remove_refs([first.id], runtime.store)

    runtime.inject_temporary_recalled([first.id])
    runtime.inject_temporary_recalled([first.id])

    first_request = runtime.materialize_provider_messages()
    second_request = runtime.materialize_provider_messages()

    assert [message["content"] for message in first_request] == [
        "Original detail",
        "Current task",
    ]
    assert [message["content"] for message in second_request] == ["Current task"]


def test_message_store_is_append_only_when_active_refs_are_removed() -> None:
    runtime = MessageRuntime()
    user = runtime.append_user("Old")
    runtime.append_assistant("New")

    runtime.active_window.remove_refs([user.id], runtime.store)

    assert runtime.store.get(user.id).content == "Old"
    assert [message.content for message in runtime.materialize_active()] == ["New"]


def test_active_window_protects_tool_use_tool_result_pairs() -> None:
    runtime = MessageRuntime()
    assistant = runtime.append_assistant(
        "Need tool.",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    result = runtime.append_tool_result("call_1", "file content")

    try:
        runtime.active_window.remove_refs([assistant.id], runtime.store)
    except ToolPairWindowError as error:
        assert "tool pair" in str(error)
    else:
        raise AssertionError("Expected ToolPairWindowError")

    runtime.active_window.remove_refs([assistant.id, result.id], runtime.store)

    assert runtime.materialize_active() == []


def test_tool_pair_validation_ignores_temporary_recalled_refs() -> None:
    runtime = MessageRuntime()
    old_assistant = runtime.append_assistant(
        "Old tool call",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    old_result = runtime.append_tool_result("call_1", "old file content")
    runtime.active_window.remove_refs([old_assistant.id, old_result.id], runtime.store)

    current_assistant = runtime.append_assistant(
        "Current tool call",
        tool_calls=[ToolCall(id="call_1", name="read_file")],
    )
    current_result = runtime.append_tool_result("call_1", "current file content")
    runtime.inject_temporary_recalled([old_assistant.id, old_result.id])

    runtime.active_window.remove_refs(
        [current_assistant.id, current_result.id],
        runtime.store,
    )

    assert [message.id for message in runtime.materialize_active()] == [
        old_assistant.id,
        old_result.id,
    ]
