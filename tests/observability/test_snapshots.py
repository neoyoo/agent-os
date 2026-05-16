from agentos.observability.config import CapturePolicy
from agentos.observability.snapshots import (
    build_provider_request_snapshot,
    build_provider_response_snapshot,
    build_tool_call_snapshot,
    build_tool_result_snapshot,
    stable_sha256,
)
from agentos.providers import (
    ProviderFunctionSpec,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
    provider_message_to_dict,
    provider_tool_spec_to_dict,
)
from agentos.capabilities import ToolExecutionResult


def test_provider_request_snapshot_metadata_mode_records_lengths_and_hashes_only() -> None:
    request = ProviderRequest(
        system="system secret",
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            ProviderToolSpec(
                function=ProviderFunctionSpec(
                    name="read_file",
                    description="Read file.",
                    parameters={"type": "object"},
                ),
            ),
        ],
    )

    snapshot = build_provider_request_snapshot(
        request,
        CapturePolicy.metadata_only(),
    )

    assert snapshot.system is None
    assert snapshot.messages is None
    assert snapshot.tools is None
    assert snapshot.system_length == len("system secret")
    assert snapshot.message_count == 1
    assert snapshot.tool_count == 1
    assert snapshot.system_sha256 == stable_sha256("system secret")
    assert snapshot.messages_sha256 == stable_sha256(
        [provider_message_to_dict(message) for message in request.messages],
    )
    assert snapshot.tools_sha256 == stable_sha256(
        [provider_tool_spec_to_dict(tool) for tool in request.tools],
    )


def test_provider_request_snapshot_full_mode_captures_payloads() -> None:
    request = ProviderRequest(
        system="system text",
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            ProviderToolSpec(
                function=ProviderFunctionSpec(
                    name="read_file",
                    description="Read file.",
                    parameters={"type": "object"},
                ),
            ),
        ],
    )

    snapshot = build_provider_request_snapshot(
        request,
        CapturePolicy.full_for_local_development(),
    )

    assert snapshot.system == "system text"
    assert snapshot.messages == ({"role": "user", "content": "hello"},)
    assert snapshot.tools == (
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file.",
                "parameters": {"type": "object"},
            },
        },
    )


def test_provider_response_snapshot_includes_tool_calls_stop_reason_and_usage() -> None:
    response = ProviderResponse(
        content="done",
        tool_calls=[
            ProviderToolCall(
                id="call_1",
                name="read_file",
                arguments={"path": "pyproject.toml"},
            ),
        ],
        stop_reason="tool_calls",
        usage=ProviderUsage(input_tokens=10, output_tokens=5),
        model="gpt-test",
        provider_name="openai",
        response_id="resp_1",
    )

    snapshot = build_provider_response_snapshot(
        response,
        CapturePolicy.full_for_local_development(),
    )

    assert snapshot.content == "done"
    assert snapshot.content_length == 4
    assert snapshot.content_sha256 == stable_sha256("done")
    assert snapshot.tool_calls[0].id == "call_1"
    assert snapshot.tool_calls[0].arguments == {"path": "pyproject.toml"}
    assert snapshot.stop_reason == "tool_calls"
    assert snapshot.usage == ProviderUsage(input_tokens=10, output_tokens=5)
    assert snapshot.model == "gpt-test"
    assert snapshot.provider_name == "openai"
    assert snapshot.response_id == "resp_1"


def test_tool_snapshots_respect_capture_policy() -> None:
    call = ProviderToolCall(
        id="call_1",
        name="read_file",
        arguments={"path": "pyproject.toml"},
    )
    result = ToolExecutionResult(tool_call_id="call_1", content="file content")

    metadata_call = build_tool_call_snapshot(call, CapturePolicy.metadata_only())
    metadata_result = build_tool_result_snapshot(result, CapturePolicy.metadata_only())
    full_call = build_tool_call_snapshot(call, CapturePolicy.full_for_local_development())
    full_result = build_tool_result_snapshot(result, CapturePolicy.full_for_local_development())

    assert metadata_call.arguments is None
    assert metadata_call.arguments_sha256 == stable_sha256(call.arguments)
    assert metadata_result.content is None
    assert metadata_result.content_sha256 == stable_sha256("file content")
    assert full_call.arguments == {"path": "pyproject.toml"}
    assert full_result.content == "file content"


def test_stable_sha256_is_independent_of_dict_ordering() -> None:
    assert stable_sha256({"a": 1, "b": 2}) == stable_sha256({"b": 2, "a": 1})


def test_response_snapshot_hides_thinking_by_default() -> None:
    snapshot = build_provider_response_snapshot(
        ProviderResponse(content="answer", thinking_content="secret"),
        CapturePolicy.metadata_only(),
    )

    assert snapshot.thinking_content is None
    assert snapshot.thinking_length == 6


def test_response_snapshot_captures_thinking_in_full_mode() -> None:
    snapshot = build_provider_response_snapshot(
        ProviderResponse(content="answer", thinking_content="secret"),
        CapturePolicy.full_for_local_development(),
    )

    assert snapshot.thinking_content == "secret"
    assert snapshot.thinking_length == 6
