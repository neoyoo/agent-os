from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest

from agentos.channels.a2a import (
    A2AAgentCard,
    A2AAgentSkill,
    AllowAllA2AInboundAuthPolicy,
    StaticBearerA2AAuthProvider,
)
from agentos.multi import TaskRecord, TaskRequest, TaskResult, TaskTable
from tests.multi.helpers import build_agent_with_response


class FakeTransport:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.posts: list[
            tuple[str, dict[str, object], float, dict[str, str] | None]
        ] = []
        self.response = response

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.posts.append((url, payload, timeout_seconds, headers))
        if self.response is not None:
            return self.response
        response = {
            "jsonrpc": "2.0",
            "result": {
                "task": {
                    "id": "task_remote",
                    "contextId": "ctx_1",
                    "status": {"state": "completed"},
                    "messages": [
                        {
                            "messageId": "msg_remote",
                            "role": "agent",
                            "parts": [{"kind": "text", "text": "remote ok"}],
                            "contextId": "ctx_1",
                            "taskId": "task_remote",
                        },
                    ],
                },
            },
        }
        if "id" in payload:
            response["id"] = payload["id"]
        return response

    def get_json(
        self,
        url: str,
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.posts.append((url, {"method": "GET"}, timeout_seconds, headers))
        if self.response is not None:
            return self.response
        return {
            "jsonrpc": "2.0",
            "result": {
                "task": {
                    "id": "task_remote",
                    "contextId": "ctx_remote",
                    "status": {"state": "completed"},
                },
            },
        }


class FakeSseTransport(FakeTransport):
    def __init__(self, chunks: tuple[str | bytes, ...]) -> None:
        super().__init__()
        self.chunks = chunks

    def post_sse(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[str | bytes, ...]:
        self.posts.append((url, payload, timeout_seconds, headers))
        return self.chunks


def task_record(
    *,
    task_id: str = "task_1",
    status: str = "queued",
    result: TaskResult | None = None,
    cancel_requested_at: float | None = None,
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker",
        request=TaskRequest(task_id=task_id, instruction="do work"),
        status=status,  # type: ignore[arg-type]
        created_at=1.0,
        deadline_at=10.0,
        result=result,
        worker_id="worker-1",
        attempt=1,
        cancel_requested_at=cancel_requested_at,
    )


def hs256_jwt(claims: dict[str, object], *, secret: bytes = b"oidc-secret") -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _base64url_json(header),
            _base64url_json(claims),
        ],
    )
    signature = hmac.new(
        secret,
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_base64url(signature)}"


def _base64url_json(payload: dict[str, object]) -> str:
    return _base64url(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_a2a_message_and_task_round_trip() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2ATask,
        a2a_message_from_dict,
        a2a_message_to_dict,
        a2a_task_from_dict,
        a2a_task_to_dict,
    )

    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("hello"),),
        message_id="msg_1",
        context_id="ctx_1",
        task_id="task_1",
        metadata={"tenant": "acme"},
    )
    task = A2ATask(
        task_id="task_1",
        context_id="ctx_1",
        state="working",
        messages=(message,),
        metadata={"priority": "high"},
    )

    payload = a2a_message_to_dict(message)

    assert payload["parts"] == [{"text": "hello"}]
    assert a2a_message_from_dict(payload) == message
    assert a2a_message_from_dict(
        {
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
            "messageId": "msg_1",
            "contextId": "ctx_1",
            "taskId": "task_1",
            "metadata": {"tenant": "acme"},
        },
    ) == message
    assert a2a_task_from_dict(a2a_task_to_dict(task)) == task


def test_a2a_message_file_and_data_parts_round_trip_with_wrapper_shape() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        a2a_message_from_dict,
        a2a_message_to_dict,
    )

    message = A2AMessage(
        role="user",
        parts=(
            A2AMessagePart.from_file_bytes(
                "iVBORw0KGgo=",
                filename="diagram.png",
                media_type="image/png",
            ),
            A2AMessagePart.from_file_url(
                "https://files.example/report.pdf",
                filename="report.pdf",
                media_type="application/pdf",
            ),
            A2AMessagePart.from_data(
                {"order_id": "ord_1", "quantity": 3},
                media_type="application/json",
            ),
        ),
        message_id="msg_payload",
    )

    payload = a2a_message_to_dict(message)

    assert payload["parts"] == [
        {
            "raw": "iVBORw0KGgo=",
            "filename": "diagram.png",
            "mediaType": "image/png",
        },
        {
            "url": "https://files.example/report.pdf",
            "filename": "report.pdf",
            "mediaType": "application/pdf",
        },
        {
            "data": {"order_id": "ord_1", "quantity": 3},
            "mediaType": "application/json",
        },
    ]
    assert a2a_message_from_dict(payload) == message


def test_a2a_message_part_parser_accepts_legacy_file_and_data_parts() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessagePart,
        a2a_message_part_from_dict,
    )

    assert a2a_message_part_from_dict(
        {
            "kind": "file",
            "file": {
                "name": "diagram.png",
                "mimeType": "image/png",
                "fileWithBytes": "iVBORw0KGgo=",
            },
        },
    ) == A2AMessagePart.from_file_bytes(
        "iVBORw0KGgo=",
        filename="diagram.png",
        media_type="image/png",
    )
    assert a2a_message_part_from_dict(
        {
            "kind": "file",
            "file": {
                "name": "report.pdf",
                "mimeType": "application/pdf",
                "uri": "https://files.example/report.pdf",
            },
        },
    ) == A2AMessagePart.from_file_url(
        "https://files.example/report.pdf",
        filename="report.pdf",
        media_type="application/pdf",
    )
    assert a2a_message_part_from_dict(
        {
            "kind": "data",
            "data": {"order_id": "ord_1"},
            "metadata": {"mediaType": "application/json"},
        },
    ) == A2AMessagePart.from_data(
        {"order_id": "ord_1"},
        media_type="application/json",
    )


def test_a2a_message_part_parser_rejects_ambiguous_wrapper_fields() -> None:
    from agentos.channels.a2a_operations import a2a_message_part_from_dict

    with pytest.raises(ValueError, match="exactly one payload"):
        a2a_message_part_from_dict(
            {
                "text": "hello",
                "data": {"unexpected": True},
            },
        )


def test_a2a_artifact_round_trip_uses_parts_shape() -> None:
    from agentos.channels.a2a_operations import (
        A2AArtifact,
        A2AMessagePart,
        a2a_artifact_from_dict,
        a2a_artifact_to_dict,
    )

    artifact = A2AArtifact(
        artifact_id="artifact_1",
        parts=(
            A2AMessagePart.from_text("summary"),
            A2AMessagePart.from_data(
                {"path": "artifact.txt"},
                media_type="application/json",
            ),
        ),
        name="Result bundle",
        description="Worker output.",
        metadata={"tenant": "acme"},
    )

    payload = a2a_artifact_to_dict(artifact)

    assert payload == {
        "artifactId": "artifact_1",
        "name": "Result bundle",
        "description": "Worker output.",
        "parts": [
            {"text": "summary"},
            {
                "data": {"path": "artifact.txt"},
                "mediaType": "application/json",
            },
        ],
        "metadata": {"tenant": "acme"},
    }
    assert a2a_artifact_from_dict(payload) == artifact


def test_a2a_task_serializes_artifacts_as_protocol_objects() -> None:
    from agentos.channels.a2a_operations import (
        A2AArtifact,
        A2AMessagePart,
        A2ATask,
        a2a_task_from_dict,
        a2a_task_to_dict,
    )

    artifact = A2AArtifact(
        artifact_id="artifact_1",
        parts=(A2AMessagePart.from_file_url("https://files.example/out.txt"),),
        name="Output",
    )
    task = A2ATask(
        task_id="task_1",
        context_id="ctx_1",
        state="completed",
        artifacts=(artifact,),
    )

    payload = a2a_task_to_dict(task)

    assert payload["artifacts"] == [
        {
            "artifactId": "artifact_1",
            "name": "Output",
            "parts": [{"url": "https://files.example/out.txt"}],
        },
    ]
    assert a2a_task_from_dict(payload) == task


def test_a2a_task_subscription_event_uses_status_update_wrapper() -> None:
    from agentos.channels.a2a_operations import (
        A2ATask,
        A2ATaskSubscriptionEvent,
        a2a_task_subscription_event_from_dict,
        a2a_task_subscription_event_to_dict,
    )

    event = A2ATaskSubscriptionEvent(
        event_id="2",
        task=A2ATask(
            task_id="task_1",
            context_id="ctx_1",
            state="working",
            metadata={"workerId": "worker-1"},
        ),
        final=False,
    )

    payload = a2a_task_subscription_event_to_dict(event)

    assert payload == {
        "statusUpdate": {
            "taskId": "task_1",
            "contextId": "ctx_1",
            "status": {"state": "working"},
            "metadata": {
                "workerId": "worker-1",
                "version": "2",
                "final": False,
            },
        },
    }
    assert a2a_task_subscription_event_from_dict(payload) == event
    assert a2a_task_subscription_event_from_dict(
        {
            "kind": "status-update",
            "final": False,
            "task": {
                "id": "task_1",
                "contextId": "ctx_1",
                "status": {"state": "working"},
                "metadata": {"workerId": "worker-1", "version": "2"},
            },
        },
    ) == event


def test_a2a_task_artifact_update_event_uses_artifact_update_wrapper() -> None:
    from agentos.channels.a2a_operations import (
        A2AArtifact,
        A2AMessagePart,
        A2ATaskArtifactUpdateEvent,
        a2a_task_artifact_update_event_from_dict,
        a2a_task_artifact_update_event_to_dict,
    )

    event = A2ATaskArtifactUpdateEvent(
        event_id="3",
        task_id="task_1",
        context_id="ctx_1",
        artifact=A2AArtifact(
            artifact_id="artifact_1",
            parts=(A2AMessagePart.from_text("chunk"),),
        ),
        append=True,
        last_chunk=True,
        metadata={"source": "worker"},
    )

    payload = a2a_task_artifact_update_event_to_dict(event)

    assert payload == {
        "artifactUpdate": {
            "taskId": "task_1",
            "contextId": "ctx_1",
            "artifact": {
                "artifactId": "artifact_1",
                "parts": [{"text": "chunk"}],
            },
            "append": True,
            "lastChunk": True,
            "metadata": {
                "source": "worker",
                "version": "3",
            },
        },
    }
    assert a2a_task_artifact_update_event_from_dict(payload) == event


def test_a2a_operation_request_and_response_round_trip() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationError,
        A2AOperationRequest,
        A2AOperationResponse,
        A2ATask,
        a2a_operation_request_from_dict,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
        a2a_operation_response_to_dict,
    )

    message = A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),))
    request = A2AOperationRequest.message_send(message, request_id="req_1")
    response = A2AOperationResponse(
        request_id="req_1",
        task=A2ATask(
            task_id="task_1",
            context_id="ctx_1",
            state="completed",
            messages=(
                A2AMessage(
                    role="agent",
                    parts=(A2AMessagePart.from_text("ok"),),
                    task_id="task_1",
                ),
            ),
        ),
    )
    error = A2AOperationResponse(
        request_id="req_2",
        error=A2AOperationError(code=-32602, message="invalid params"),
    )

    assert request.method == "SendMessage"
    assert (
        a2a_operation_request_from_dict(a2a_operation_request_to_dict(request))
        == request
    )
    assert (
        a2a_operation_response_from_dict(a2a_operation_response_to_dict(response))
        == response
    )
    assert (
        a2a_operation_response_from_dict(a2a_operation_response_to_dict(error))
        == error
    )


def test_a2a_official_operation_names_map_to_json_rpc_methods() -> None:
    from agentos.channels.a2a_operations import (
        A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD,
        A2A_JSONRPC_METHOD_TO_OFFICIAL_OPERATION,
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        a2a_official_operation_for_method,
        a2a_operation_request_to_dict,
    )

    send = A2AOperationRequest.message_send(
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
    )
    stream = A2AOperationRequest.message_stream(
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
    )
    subscribe = A2AOperationRequest.task_resubscribe("task_1")

    assert A2A_OFFICIAL_OPERATION_TO_JSONRPC_METHOD == {
        "SendMessage": "SendMessage",
        "SendStreamingMessage": "SendStreamingMessage",
        "SubscribeToTask": "SubscribeToTask",
    }
    assert A2A_JSONRPC_METHOD_TO_OFFICIAL_OPERATION == {
        "SendMessage": "SendMessage",
        "SendStreamingMessage": "SendStreamingMessage",
        "SubscribeToTask": "SubscribeToTask",
        "message/send": "SendMessage",
        "message/stream": "SendStreamingMessage",
        "tasks/resubscribe": "SubscribeToTask",
    }
    assert send.operation_name == "SendMessage"
    assert stream.operation_name == "SendStreamingMessage"
    assert subscribe.operation_name == "SubscribeToTask"
    assert a2a_official_operation_for_method("agentos/run") is None
    assert a2a_operation_request_to_dict(send)["method"] == "SendMessage"


def test_a2a_task_resubscribe_operation_request_response_round_trip() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationRequest,
        A2AOperationResponse,
        A2ATask,
        A2ATaskSubscriptionEvent,
        a2a_operation_request_from_dict,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
        a2a_operation_response_to_dict,
    )

    request = A2AOperationRequest.task_resubscribe(
        "task_1",
        after_event_id=4,
        request_id="req_subscribe",
    )
    event = A2ATaskSubscriptionEvent(
        event_id="5",
        task=A2ATask(
            task_id="task_1",
            context_id="ctx_1",
            state="working",
            metadata={"version": 5},
        ),
        final=False,
    )
    response = A2AOperationResponse(
        request_id="req_subscribe",
        task_event=event,
    )

    assert a2a_operation_request_to_dict(request) == {
        "jsonrpc": "2.0",
        "id": "req_subscribe",
        "method": "SubscribeToTask",
        "params": {"id": "task_1", "afterEventId": 4},
    }
    assert a2a_operation_request_from_dict(
        a2a_operation_request_to_dict(request),
    ) == request
    payload = a2a_operation_response_to_dict(response)
    assert payload["result"]["statusUpdate"]["taskId"] == "task_1"
    assert payload["result"]["statusUpdate"]["metadata"]["version"] == "5"
    assert a2a_operation_response_from_dict(payload) == response


def test_a2a_message_stream_operation_request_round_trip() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        a2a_operation_request_from_dict,
        a2a_operation_request_to_dict,
    )

    message = A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),))
    request = A2AOperationRequest.message_stream(message, request_id="req_stream")

    assert a2a_operation_request_to_dict(request) == {
        "jsonrpc": "2.0",
        "id": "req_stream",
        "method": "SendStreamingMessage",
        "params": {"message": {"role": "user", "parts": [{"text": "hello"}]}},
    }
    assert (
        a2a_operation_request_from_dict(a2a_operation_request_to_dict(request))
        == request
    )


def test_parse_a2a_sse_events_maps_message_stream_payloads() -> None:
    from agentos.channels.a2a_operations import parse_a2a_sse_events

    chunks = [
        b": heartbeat\n\n",
        (
            'event: task\n'
            'data: {"task": {"id": "task_stream", "contextId": "ctx_1", '
            '"status": {"state": "submitted"}}}\n\n'
        ),
        (
            'event: message\n'
            'data: {"message": {"messageId": "msg_1", "role": "agent", '
            '"parts": [{"text": "hello"}], "contextId": "ctx_1", '
            '"taskId": "task_stream"}}\n\n'
        ),
        (
            'event: task_status_update\n'
            'data: {"statusUpdate": {"taskId": "task_stream", '
            '"contextId": "ctx_1", "status": {"state": "working"}, '
            '"metadata": {"version": "2"}}}\n\n'
        ),
        (
            'event: task_artifact_update\n'
            'data: {"artifactUpdate": {"taskId": "task_stream", '
            '"contextId": "ctx_1", "artifact": {"artifactId": "artifact_1", '
            '"parts": [{"text": "part "}, {"text": "two"}]}, '
            '"append": true, "lastChunk": false, "metadata": {"version": "3"}}}'
            '\n\n'
        ),
    ]

    events = tuple(parse_a2a_sse_events(chunks))

    assert len(events) == 4
    assert events[0].event == "task"
    assert events[0].task is not None
    assert events[0].task.task_id == "task_stream"
    assert events[1].event == "message"
    assert events[1].message is not None
    assert events[1].message.parts[0].text == "hello"
    assert events[2].event == "task_status_update"
    assert events[2].task_event is not None
    assert events[2].task_event.event_id == "2"
    assert events[2].task_event.task.state == "working"
    assert events[3].event == "task_artifact_update"
    assert events[3].artifact_event is not None
    assert events[3].artifact_event.event_id == "3"
    assert events[3].artifact_event.artifact.parts[1].text == "two"


def test_agent_a2a_operation_runner_handles_message_send() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        AgentA2AOperationRunner,
    )

    runner = AgentA2AOperationRunner(build_agent_with_response("agent ok"))

    result = asyncio.run(
        runner.send_message(
            A2AMessage(
                role="user",
                parts=(A2AMessagePart.from_text("hello"),),
                context_id="ctx_1",
            ),
        ),
    )

    assert result.state == "completed"
    assert result.context_id == "ctx_1"
    assert result.messages[-1].role == "agent"
    assert result.messages[-1].parts[0].text == "agent ok"


def test_a2a_operation_server_defaults_to_rejecting_unauthenticated_peers() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2ATask,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    class CountingRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def send_message(self, message: A2AMessage) -> A2ATask:
            self.calls += 1
            return A2ATask(task_id="task_1", context_id=None, state="completed")

    runner = CountingRunner()
    server = A2AOperationServer(runner)
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_default_auth",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload)),
    )

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.message == "unauthorized peer"
    assert runner.calls == 0


def test_a2a_operation_server_handles_message_send() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("server ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload)),
    )

    assert response.request_id == "req_1"
    assert response.error is None
    assert response.task is not None
    assert response.task.state == "completed"
    assert response.task.messages[-1].parts[0].text == "server ok"


def test_a2a_operation_server_message_send_path_rejects_stream_method() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2ATask,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    class CountingRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def send_message(self, message: A2AMessage) -> A2ATask:
            self.calls += 1
            return A2ATask(task_id="task_1", context_id=None, state="completed")

    runner = CountingRunner()
    server = A2AOperationServer(
        runner,
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_stream(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_wrong_path",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(server.handle_message_send(payload)),
    )

    assert response.request_id == "req_wrong_path"
    assert response.error is not None
    assert response.error.code == -32601
    assert runner.calls == 0


def test_a2a_operation_server_does_not_disclose_runner_exception_text() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2ATask,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    class FailingRunner:
        async def send_message(self, message: A2AMessage) -> A2ATask:
            raise RuntimeError("database password=secret-token failed")

    server = A2AOperationServer(
        FailingRunner(),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_secret_error",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload)),
    )

    assert response.error is not None
    assert response.error.code == -32603
    assert response.error.message == "internal error"
    assert response.error.data is None


def test_a2a_operation_server_handles_message_stream_json_rpc() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("stream ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                a2a_operation_request_to_dict(
                    A2AOperationRequest.message_stream(
                        A2AMessage(
                            role="user",
                            parts=(A2AMessagePart.from_text("hello"),),
                        ),
                        request_id="req_stream",
                    ),
                ),
            ),
        ),
    )

    assert response.request_id == "req_stream"
    assert response.error is None
    assert response.task is not None
    assert response.task.state == "completed"
    assert response.task.messages[-1].parts[0].text == "stream ok"


def test_a2a_operation_server_message_stream_path_rejects_send_method() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2ATask,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    class CountingRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def send_message(self, message: A2AMessage) -> A2ATask:
            self.calls += 1
            return A2ATask(task_id="task_1", context_id=None, state="completed")

    runner = CountingRunner()
    server = A2AOperationServer(
        runner,
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_wrong_path",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(server.handle_message_stream(payload)),
    )

    assert response.request_id == "req_wrong_path"
    assert response.error is not None
    assert response.error.code == -32601
    assert runner.calls == 0


def test_a2a_operation_server_returns_protocol_errors() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
    )

    invalid = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation({})),
    )
    unsupported = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                {
                    "jsonrpc": "2.0",
                    "id": "req_2",
                    "method": "agent/teleport",
                    "params": {},
                },
            ),
        ),
    )

    assert invalid.error is not None
    assert invalid.error.code == -32602
    assert unsupported.error is not None
    assert unsupported.error.code == -32601


def test_a2a_operation_server_requires_declared_extensions() -> None:
    from agentos.channels.a2a import A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationPolicy,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    required_uri = "https://extensions.example/trace-artifacts/v1"
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("extension ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            local_extensions=(
                A2AAgentExtension(uri=required_uri, required=True),
            ),
        ),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_ext",
        ),
    )

    missing = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(payload, headers={"A2A-Version": "1.0"}),
        ),
    )
    allowed = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={
                    "A2A-Version": "1.0",
                    "A2A-Extensions": required_uri,
                },
            ),
        ),
    )

    assert missing.request_id == "req_ext"
    assert missing.error is not None
    assert missing.error.code == -32008
    assert missing.error.message == "extension support required"
    assert missing.error.data == {
        "type": "https://a2a-protocol.org/errors/extension-support-required",
        "title": "Extension Support Required",
        "status": 400,
        "detail": (
            "The requested A2A operation requires unsupported extensions: "
            "https://extensions.example/trace-artifacts/v1"
        ),
        "missingExtensions": [required_uri],
        "supportedExtensions": [required_uri],
        "requestedExtensions": [],
    }
    assert allowed.error is None
    assert allowed.task is not None
    assert allowed.task.messages[-1].parts[0].text == "extension ok"


def test_a2a_operation_server_degrades_optional_unsupported_extensions() -> None:
    from agentos.channels.a2a import A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationPolicy,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("degraded ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            local_extensions=(
                A2AAgentExtension(uri="https://extensions.example/known/v1"),
            ),
        ),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_optional",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={
                    "A2A-Version": "1.0",
                    "A2A-Extensions": (
                        "https://extensions.example/known/v1, "
                        "https://extensions.example/unknown/v1"
                    ),
                },
            ),
        ),
    )

    assert response.error is None
    assert response.task is not None
    assert response.task.messages[-1].parts[0].text == "degraded ok"


def test_a2a_operation_server_enforces_inbound_peer_auth() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("authorized")),
        inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    missing = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload)),
    )
    wrong = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": "Bearer wrong"},
            ),
        ),
    )
    allowed = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": "Bearer peer-token"},
            ),
        ),
    )

    assert missing.error is not None
    assert missing.error.code == -32030
    assert missing.error.message == "unauthorized peer"
    assert missing.error.data is None
    assert wrong.error is not None
    assert wrong.error.code == -32030
    assert allowed.error is None
    assert allowed.task is not None
    assert allowed.task.messages[-1].parts[0].text == "authorized"


def test_a2a_operation_server_rate_limits_per_peer_before_runner() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2APeerIdResolver,
        A2ATask,
        PeerKeyA2AOperationRateLimitPolicy,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )
    from agentos.channels.rate_limit import SlidingWindowRateLimiter

    class HeaderPeerResolver:
        def peer_id_for_headers(self, headers: dict[str, str]) -> str | None:
            return headers.get("X-A2A-Peer")

    class CountingRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def send_message(self, message: A2AMessage) -> A2ATask:
            self.calls += 1
            return A2ATask(
                task_id=f"task_{self.calls}",
                context_id=message.context_id,
                state="completed",
                messages=(
                    A2AMessage(
                        role="agent",
                        parts=(A2AMessagePart.from_text("allowed"),),
                    ),
                ),
            )

    resolver: A2APeerIdResolver = HeaderPeerResolver()
    runner = CountingRunner()
    server = A2AOperationServer(
        runner,
        inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
        rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(
            peer_id_resolver=resolver,
            rate_limiter=SlidingWindowRateLimiter(
                max_requests=1,
                window_seconds=60,
                now=lambda: 0.0,
            ),
        ),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_rate",
        ),
    )
    headers = {
        "Authorization": "Bearer peer-token",
        "X-A2A-Peer": "peer-a",
    }

    allowed = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload, headers=headers)),
    )
    denied = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(payload, headers=headers)),
    )
    other_peer = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={
                    "Authorization": "Bearer peer-token",
                    "X-A2A-Peer": "peer-b",
                },
            ),
        ),
    )

    assert allowed.error is None
    assert denied.request_id == "req_rate"
    assert denied.error is not None
    assert denied.error.code == -32029
    assert denied.error.message == "rate limit exceeded"
    assert denied.error.data == {
        "type": "https://a2a-protocol.org/errors/rate-limit-exceeded",
        "title": "Rate Limit Exceeded",
        "status": 429,
        "retryAfterSeconds": 60,
    }
    assert other_peer.error is None
    assert runner.calls == 2


def test_a2a_operation_server_handles_task_resubscribe_json_rpc() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    initial = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                a2a_operation_request_to_dict(
                    A2AOperationRequest.task_resubscribe(
                        "task_1",
                        request_id="req_subscribe",
                    ),
                ),
            ),
        ),
    )
    no_update = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                a2a_operation_request_to_dict(
                    A2AOperationRequest.task_resubscribe(
                        "task_1",
                        after_event_id=0,
                        request_id="req_after",
                    ),
                ),
            ),
        ),
    )
    missing = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                a2a_operation_request_to_dict(
                    A2AOperationRequest.task_resubscribe(
                        "missing",
                        request_id="req_missing",
                    ),
                ),
            ),
        ),
    )

    assert initial.request_id == "req_subscribe"
    assert initial.error is None
    assert initial.task_event is not None
    assert initial.task_event.event_id == "0"
    assert initial.task_event.task.task_id == "task_1"
    assert initial.task_event.task.state == "submitted"
    assert no_update.error is None
    assert no_update.task_event is None
    assert missing.request_id == "req_missing"
    assert missing.error is not None
    assert missing.error.code == -32001


def test_a2a_operation_rate_limit_keys_include_peer_operation_and_resource() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationServer,
        A2APeerIdResolver,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        PeerKeyA2AOperationRateLimitPolicy,
        TaskStoreA2ATaskLifecycleRunner,
        A2AOperationRequest,
        a2a_operation_request_to_dict,
    )
    from agentos.channels.rate_limit import RateLimitDecision

    class HeaderPeerResolver:
        def peer_id_for_headers(self, headers: dict[str, str]) -> str | None:
            return headers.get("X-A2A-Peer")

    class RecordingLimiter:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def check(self, key: str) -> RateLimitDecision:
            self.keys.append(key)
            return RateLimitDecision(True, 0)

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    limiter = RecordingLimiter()
    resolver: A2APeerIdResolver = HeaderPeerResolver()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=InMemoryA2APushNotificationConfigStore(),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        rate_limit_policy=PeerKeyA2AOperationRateLimitPolicy(
            peer_id_resolver=resolver,
            rate_limiter=limiter,
        ),
    )
    headers = {"X-A2A-Peer": "peer-a"}

    asyncio.run(
        server.handle_operation(
            a2a_operation_request_to_dict(
                A2AOperationRequest.message_stream(
                    A2AMessage(
                        role="user",
                        parts=(A2AMessagePart.from_text("hello"),),
                    ),
                ),
            ),
            headers=headers,
        ),
    )
    server.handle_task_get("task_1", headers=headers)
    server.handle_task_resubscribe("task_1", after_event_id=0, headers=headers)
    server.handle_push_notification_config_create(
        "task_1",
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        headers=headers,
    )

    assert limiter.keys == [
        "a2a:peer=peer-a:operation=SendStreamingMessage",
        "a2a:peer=peer-a:operation=tasks_get:task=task_1:resource=task/task_1",
        (
            "a2a:peer=peer-a:operation=SubscribeToTask:"
            "task=task_1:resource=task/task_1"
        ),
        (
            "a2a:peer=peer-a:operation=pushNotificationConfigs_create:"
            "task=task_1:resource=pushNotificationConfig/cfg_1"
        ),
    ]


def test_rotating_bearer_auth_provider_uses_current_active_credential() -> None:
    from agentos.channels.a2a import (
        A2ABearerCredential,
        A2ACredentialRotationError,
        RotatingBearerA2AAuthProvider,
        RotatingBearerA2ACredentialStore,
    )

    store = RotatingBearerA2ACredentialStore(
        credentials=(
            A2ABearerCredential(
                key_id="old",
                peer_id="remote-agent",
                token="old-token",
                not_before=10,
                not_after=30,
            ),
            A2ABearerCredential(
                key_id="new",
                peer_id="remote-agent",
                token="new-token",
                not_before=20,
                not_after=50,
            ),
        ),
        current_key_id="new",
        clock=lambda: 25.0,
    )
    provider = RotatingBearerA2AAuthProvider(store)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
    )

    assert provider.headers_for_card(card) == {
        "Authorization": "Bearer new-token",
    }
    assert store.active_key_ids() == ("new", "old")
    assert "old-token" not in repr(store)
    assert "new-token" not in repr(store)
    assert "new-token" not in repr(provider)

    inactive_store = RotatingBearerA2ACredentialStore(
        credentials=(
            A2ABearerCredential(
                key_id="future",
                peer_id="remote-agent",
                token="future-token",
                not_before=40,
            ),
        ),
        current_key_id="future",
        clock=lambda: 25.0,
    )
    with pytest.raises(
        A2ACredentialRotationError,
        match="current bearer credential is not active",
    ):
        RotatingBearerA2AAuthProvider(inactive_store).headers_for_card(card)


def test_rotating_bearer_inbound_auth_accepts_overlap_and_rejects_denied_states() -> None:
    from agentos.channels.a2a import (
        A2ABearerCredential,
        A2AInboundAuthError,
        RotatingBearerA2AInboundAuthPolicy,
        RotatingBearerA2ACredentialStore,
    )

    store = RotatingBearerA2ACredentialStore(
        credentials=(
            A2ABearerCredential(
                key_id="old",
                peer_id="researcher",
                token="old-token",
                not_before=10,
                not_after=30,
            ),
            A2ABearerCredential(
                key_id="new",
                peer_id="researcher",
                token="new-token",
                not_before=20,
                not_after=50,
            ),
            A2ABearerCredential(
                key_id="expired",
                peer_id="researcher",
                token="expired-token",
                not_before=1,
                not_after=5,
            ),
            A2ABearerCredential(
                key_id="future",
                peer_id="researcher",
                token="future-token",
                not_before=40,
                not_after=60,
            ),
            A2ABearerCredential(
                key_id="revoked",
                peer_id="researcher",
                token="revoked-token",
                not_before=10,
                not_after=50,
                revoked=True,
            ),
            A2ABearerCredential(
                key_id="blocked-peer",
                peer_id="blocked",
                token="blocked-token",
                not_before=10,
                not_after=50,
            ),
        ),
        current_key_id="new",
        clock=lambda: 25.0,
    )
    policy = RotatingBearerA2AInboundAuthPolicy(
        store,
        allowed_peer_ids=("researcher",),
    )

    policy.authorize({"Authorization": "Bearer old-token"})
    policy.authorize({"Authorization": "Bearer new-token"})

    for headers in (
        {},
        {"Authorization": "Token new-token"},
        {"Authorization": "Bearer expired-token"},
        {"Authorization": "Bearer future-token"},
        {"Authorization": "Bearer revoked-token"},
        {"Authorization": "Bearer blocked-token"},
        {"Authorization": "Bearer unknown-token"},
    ):
        with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
            policy.authorize(headers)
    assert "new-token" not in repr(policy)


def test_rotating_bearer_inbound_auth_enforces_operation_and_resource_allow_lists() -> None:
    from agentos.channels.a2a import (
        A2ABearerCredential,
        A2AInboundAuthError,
        RotatingBearerA2AInboundAuthPolicy,
        RotatingBearerA2ACredentialStore,
    )

    store = RotatingBearerA2ACredentialStore(
        credentials=(
            A2ABearerCredential(
                key_id="researcher",
                peer_id="researcher",
                token="researcher-token",
                not_before=10,
                not_after=50,
            ),
            A2ABearerCredential(
                key_id="reviewer",
                peer_id="reviewer",
                token="reviewer-token",
                not_before=10,
                not_after=50,
            ),
        ),
        current_key_id="researcher",
        clock=lambda: 25.0,
    )
    policy = RotatingBearerA2AInboundAuthPolicy(
        store,
        allowed_operations={
            "researcher": (
                "message/send",
                "tasks/get",
                "pushNotificationConfigs/get",
            ),
            "reviewer": ("tasks/get",),
        },
        allowed_task_ids={"researcher": ("task_1",)},
        allowed_resources={
            "researcher": (
                ("task", "task_1"),
                ("pushNotificationConfig", "cfg_1"),
            ),
        },
    )
    researcher_headers = {"Authorization": "Bearer researcher-token"}
    reviewer_headers = {"Authorization": "Bearer reviewer-token"}

    policy.authorize_operation(researcher_headers, operation="message/send")
    policy.authorize_resource(
        researcher_headers,
        operation="tasks/get",
        task_id="task_1",
        resource_type="task",
        resource_id="task_1",
    )
    policy.authorize_resource(
        researcher_headers,
        operation="pushNotificationConfigs/get",
        resource_type="pushNotificationConfig",
        resource_id="cfg_1",
    )

    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        policy.authorize_operation(reviewer_headers, operation="message/send")
    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        policy.authorize_resource(
            researcher_headers,
            operation="tasks/get",
            task_id="task_2",
        )
    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        policy.authorize_resource(
            researcher_headers,
            operation="pushNotificationConfigs/get",
            resource_type="pushNotificationConfig",
            resource_id="cfg_2",
        )


def test_a2a_operation_server_accepts_oidc_claims_inbound_auth() -> None:
    from agentos.channels.a2a import (
        HmacA2AJwtVerifier,
        OidcClaimsA2AInboundAuthPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    token = hs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
        },
    )
    policy = OidcClaimsA2AInboundAuthPolicy(
        verifier=HmacA2AJwtVerifier(secret=b"oidc-secret"),
        issuer="https://issuer.example",
        audience="agentos-a2a",
        allowed_peer_ids=("researcher",),
        clock=lambda: 150.0,
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("oidc authorized")),
        inbound_auth_policy=policy,
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_oidc",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": f"Bearer {token}"},
            ),
        ),
    )

    assert response.error is None
    assert response.task is not None
    assert response.task.messages[-1].parts[0].text == "oidc authorized"
    assert token not in repr(policy)
    assert "oidc-secret" not in repr(policy)


@pytest.mark.parametrize(
    ("claims_override", "clock"),
    [
        ({"iss": "https://other-issuer.example"}, 150.0),
        ({"aud": ["other-audience"]}, 150.0),
        ({"exp": 80.0}, 150.0),
        ({"nbf": 240.0}, 150.0),
        ({"azp": "blocked-peer"}, 150.0),
    ],
)
def test_a2a_operation_server_rejects_invalid_oidc_claims_before_runner(
    claims_override: dict[str, object],
    clock: float,
) -> None:
    from agentos.channels.a2a import (
        HmacA2AJwtVerifier,
        OidcClaimsA2AInboundAuthPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    class FailingRunner:
        async def send_message(self, message: object) -> object:
            raise AssertionError("runner should not execute")

    claims: dict[str, object] = {
        "iss": "https://issuer.example",
        "aud": ["agentos-a2a"],
        "sub": "peer-subject",
        "azp": "researcher",
        "iat": 100.0,
        "nbf": 100.0,
        "exp": 200.0,
    }
    claims.update(claims_override)
    token = hs256_jwt(claims)
    policy = OidcClaimsA2AInboundAuthPolicy(
        verifier=HmacA2AJwtVerifier(secret=b"oidc-secret"),
        issuer="https://issuer.example",
        audience="agentos-a2a",
        allowed_peer_ids=("researcher",),
        clock=lambda: clock,
    )
    server = A2AOperationServer(FailingRunner(), inbound_auth_policy=policy)
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_oidc_blocked",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": f"Bearer {token}"},
            ),
        ),
    )

    assert response.error is not None
    assert response.error.code == -32030
    assert response.error.message == "unauthorized peer"


def test_a2a_operation_server_enforces_claims_tenant_rbac_for_operations() -> None:
    from agentos.channels.a2a import (
        A2ATenantRbacRule,
        ClaimsTenantRbacA2AInboundAuthPolicy,
        HmacA2AJwtVerifier,
        OidcClaimsA2AInboundAuthPolicy,
    )
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    claims_policy = OidcClaimsA2AInboundAuthPolicy(
        verifier=HmacA2AJwtVerifier(secret=b"oidc-secret"),
        issuer="https://issuer.example",
        audience="agentos-a2a",
        allowed_peer_ids=("researcher",),
        clock=lambda: 150.0,
    )
    policy = ClaimsTenantRbacA2AInboundAuthPolicy(
        claims_policy=claims_policy,
        rules=(
            A2ATenantRbacRule(
                tenant_id="tenant_acme",
                operations=("message/send",),
                roles=("agent-operator",),
            ),
        ),
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("tenant ok")),
        inbound_auth_policy=policy,
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_tenant",
        ),
    )
    allowed_token = hs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
            "tenant_id": "tenant_acme",
            "roles": ["agent-operator"],
        },
    )
    wrong_tenant_token = hs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
            "tenant_id": "tenant_other",
            "roles": ["agent-operator"],
        },
    )

    allowed = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": f"Bearer {allowed_token}"},
            ),
        ),
    )
    denied = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": f"Bearer {wrong_tenant_token}"},
            ),
        ),
    )

    assert allowed.error is None
    assert allowed.task is not None
    assert allowed.task.messages[-1].parts[0].text == "tenant ok"
    assert denied.error is not None
    assert denied.error.code == -32030
    assert denied.error.message == "unauthorized peer"
    assert "oidc-secret" not in repr(policy)


def test_claims_tenant_rbac_authorizes_scoped_task_resource() -> None:
    from agentos.channels.a2a import (
        A2AInboundAuthError,
        A2ATenantRbacRule,
        ClaimsTenantRbacA2AInboundAuthPolicy,
        HmacA2AJwtVerifier,
        OidcClaimsA2AInboundAuthPolicy,
    )

    claims_policy = OidcClaimsA2AInboundAuthPolicy(
        verifier=HmacA2AJwtVerifier(secret=b"oidc-secret"),
        issuer="https://issuer.example",
        audience="agentos-a2a",
        allowed_peer_ids=("researcher",),
        clock=lambda: 150.0,
    )
    policy = ClaimsTenantRbacA2AInboundAuthPolicy(
        claims_policy=claims_policy,
        rules=(
            A2ATenantRbacRule(
                tenant_id="tenant_acme",
                operations=("tasks/cancel",),
                scopes=("a2a:tasks.cancel",),
                resources=(("task", "task_1"),),
            ),
        ),
    )
    token = hs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
            "tenant_id": "tenant_acme",
            "scope": "a2a:tasks.read a2a:tasks.cancel",
        },
    )
    headers = {"Authorization": f"Bearer {token}"}

    policy.authorize_resource(headers, operation="tasks/cancel", task_id="task_1")
    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        policy.authorize_resource(
            headers,
            operation="tasks/cancel",
            task_id="task_2",
        )
    with pytest.raises(A2AInboundAuthError, match="unauthorized peer"):
        policy.authorize_resource(headers, operation="tasks/get", task_id="task_1")


def test_claims_tenant_rbac_supports_custom_claim_names_and_scope_lists() -> None:
    from agentos.channels.a2a import (
        A2ATenantRbacRule,
        ClaimsTenantRbacA2AInboundAuthPolicy,
        HmacA2AJwtVerifier,
        OidcClaimsA2AInboundAuthPolicy,
    )

    claims_policy = OidcClaimsA2AInboundAuthPolicy(
        verifier=HmacA2AJwtVerifier(secret=b"oidc-secret"),
        issuer="https://issuer.example",
        audience="agentos-a2a",
        allowed_peer_ids=("researcher",),
        clock=lambda: 150.0,
    )
    policy = ClaimsTenantRbacA2AInboundAuthPolicy(
        claims_policy=claims_policy,
        tenant_claim_names=("org_ids",),
        role_claim_names=("permissions",),
        scope_claim_names=("entitlements",),
        rules=(
            A2ATenantRbacRule(
                tenant_id="tenant_beta",
                operations=("*",),
                roles=("a2a-admin",),
                scopes=("a2a:*",),
                resources=(("*", "*"),),
            ),
        ),
    )
    token = hs256_jwt(
        {
            "iss": "https://issuer.example",
            "aud": ["agentos-a2a"],
            "sub": "peer-subject",
            "azp": "researcher",
            "iat": 100.0,
            "nbf": 100.0,
            "exp": 200.0,
            "org_ids": ["tenant_alpha", "tenant_beta"],
            "permissions": ["a2a-admin"],
            "entitlements": ["a2a:*"],
        },
    )

    policy.authorize_resource(
        {"Authorization": f"Bearer {token}"},
        operation="tasks/cancel",
        resource_type="pushNotificationConfig",
        resource_id="cfg_1",
    )


def test_a2a_operation_server_enforces_inbound_peer_allow_list() -> None:
    from agentos.channels.a2a import PeerAllowListA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    policy = PeerAllowListA2AInboundAuthPolicy(
        peer_tokens={
            "researcher": "token-researcher",
            "blocked": "token-blocked",
        },
        allowed_peer_ids=("researcher",),
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("authorized")),
        inbound_auth_policy=policy,
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    blocked = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": "Bearer token-blocked"},
            ),
        ),
    )
    allowed = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(
                payload,
                headers={"Authorization": "Bearer token-researcher"},
            ),
        ),
    )

    assert blocked.error is not None
    assert blocked.error.code == -32030
    assert blocked.error.message == "unauthorized peer"
    assert blocked.error.data is None
    assert allowed.error is None
    assert allowed.task is not None
    assert allowed.task.messages[-1].parts[0].text == "authorized"
    assert "token-researcher" not in repr(policy)
    assert "token-blocked" not in repr(policy)


def test_a2a_operation_server_enforces_operation_aware_inbound_auth() -> None:
    from agentos.channels.a2a import OperationAllowListA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    policy = OperationAllowListA2AInboundAuthPolicy(
        peer_tokens={"researcher": "token-researcher"},
        allowed_operations={
            "researcher": ("message/send", "tasks/get"),
        },
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("authorized")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=InMemoryA2APushNotificationConfigStore(),
        inbound_auth_policy=policy,
    )
    headers = {"Authorization": "Bearer token-researcher"}
    message_payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    message = a2a_operation_response_from_dict(
        asyncio.run(server.handle_operation(message_payload, headers=headers)),
    )
    task_get = a2a_operation_response_from_dict(
        server.handle_task_get("task_1", headers=headers),
    )
    cancel = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_1", headers=headers),
    )
    push_create = a2a_operation_response_from_dict(
        server.handle_push_notification_config_create(
            "task_1",
            A2APushNotificationConfig(
                config_id="cfg_1",
                url="https://client.example/webhook",
            ),
            headers=headers,
        ),
    )

    assert message.error is None
    assert task_get.error is None
    assert cancel.error is not None
    assert cancel.error.code == -32030
    assert push_create.error is not None
    assert push_create.error.code == -32030
    assert "token-researcher" not in repr(policy)


def test_a2a_operation_server_passes_resource_context_to_inbound_auth() -> None:
    from agentos.channels.a2a import A2AInboundAuthError
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_response_from_dict,
    )

    class RecordingResourcePolicy:
        def __init__(self) -> None:
            self.calls: list[dict[str, str | None]] = []

        def authorize_resource(
            self,
            headers: dict[str, str],
            *,
            operation: str,
            task_id: str | None = None,
            resource_type: str | None = None,
            resource_id: str | None = None,
        ) -> None:
            self.calls.append(
                {
                    "operation": operation,
                    "task_id": task_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            )
            if task_id == "task_private":
                raise A2AInboundAuthError("unauthorized peer")

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    task_store.create(task_record(task_id="task_private", status="queued"))
    config_store = InMemoryA2APushNotificationConfigStore()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=config_store,
        inbound_auth_policy=RecordingResourcePolicy(),
    )
    config = A2APushNotificationConfig(
        config_id="cfg_1",
        url="https://client.example/webhook",
    )

    task_get = a2a_operation_response_from_dict(
        server.handle_task_get("task_1", headers={}),
    )
    task_cancel = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_1", headers={}),
    )
    create = server.handle_push_notification_config_create(
        "task_1",
        config,
        headers={},
    )
    get = server.handle_push_notification_config_get(
        "task_1",
        "cfg_1",
        headers={},
    )
    listed = server.handle_push_notification_config_list("task_1", headers={})
    deleted = server.handle_push_notification_config_delete(
        "task_1",
        "cfg_1",
        headers={},
    )
    blocked = a2a_operation_response_from_dict(
        server.handle_task_get("task_private", headers={}),
    )

    assert task_get.error is None
    assert task_cancel.error is None
    assert create["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert get["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert listed["result"]["pushNotificationConfigs"][0]["id"] == "cfg_1"
    assert deleted["result"] == {}
    assert blocked.error is not None
    assert blocked.error.code == -32030
    assert server._inbound_auth_policy.calls == [
        {
            "operation": "tasks/get",
            "task_id": "task_1",
            "resource_type": "task",
            "resource_id": "task_1",
        },
        {
            "operation": "tasks/cancel",
            "task_id": "task_1",
            "resource_type": "task",
            "resource_id": "task_1",
        },
        {
            "operation": "pushNotificationConfigs/create",
            "task_id": "task_1",
            "resource_type": "pushNotificationConfig",
            "resource_id": "cfg_1",
        },
        {
            "operation": "pushNotificationConfigs/get",
            "task_id": "task_1",
            "resource_type": "pushNotificationConfig",
            "resource_id": "cfg_1",
        },
        {
            "operation": "pushNotificationConfigs/list",
            "task_id": "task_1",
            "resource_type": "pushNotificationConfig",
            "resource_id": None,
        },
        {
            "operation": "pushNotificationConfigs/delete",
            "task_id": "task_1",
            "resource_type": "pushNotificationConfig",
            "resource_id": "cfg_1",
        },
        {
            "operation": "tasks/get",
            "task_id": "task_private",
            "resource_type": "task",
            "resource_id": "task_private",
        },
    ]


def test_resource_allow_list_a2a_inbound_auth_limits_task_resources() -> None:
    from agentos.channels.a2a import ResourceAllowListA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_response_from_dict,
    )

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    task_store.create(task_record(task_id="task_2", status="queued"))
    config_store = InMemoryA2APushNotificationConfigStore()
    policy = ResourceAllowListA2AInboundAuthPolicy(
        peer_tokens={"researcher": "token-researcher"},
        allowed_operations={
            "researcher": (
                "tasks/get",
                "pushNotificationConfigs/create",
                "pushNotificationConfigs/get",
            ),
        },
        allowed_task_ids={"researcher": ("task_1",)},
        allowed_resources={
            "researcher": (
                ("task", "task_1"),
                ("pushNotificationConfig", "cfg_1"),
            ),
        },
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=config_store,
        inbound_auth_policy=policy,
    )
    headers = {"Authorization": "Bearer token-researcher"}

    allowed_get = a2a_operation_response_from_dict(
        server.handle_task_get("task_1", headers=headers),
    )
    denied_task = a2a_operation_response_from_dict(
        server.handle_task_get("task_2", headers=headers),
    )
    allowed_create = server.handle_push_notification_config_create(
        "task_1",
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        headers=headers,
    )
    denied_config = a2a_operation_response_from_dict(
        server.handle_push_notification_config_create(
            "task_1",
            A2APushNotificationConfig(
                config_id="cfg_2",
                url="https://client.example/webhook-2",
            ),
            headers=headers,
        ),
    )
    denied_operation = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_1", headers=headers),
    )

    assert allowed_get.error is None
    assert denied_task.error is not None
    assert denied_task.error.code == -32030
    assert allowed_create["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert denied_config.error is not None
    assert denied_config.error.code == -32030
    assert denied_operation.error is not None
    assert denied_operation.error.code == -32030
    assert "token-researcher" not in repr(policy)


def test_a2a_operation_server_enforces_inbound_peer_auth_for_task_and_push_routes() -> None:
    from agentos.channels.a2a import StaticBearerA2AInboundAuthPolicy
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_response_from_dict,
    )

    task_store = TaskTable()
    task_store.create(task_record(task_id="task_1", status="queued"))
    config_store = InMemoryA2APushNotificationConfigStore()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(task_store),
        push_notification_configs=config_store,
        inbound_auth_policy=StaticBearerA2AInboundAuthPolicy("peer-token"),
    )
    headers = {"Authorization": "Bearer peer-token"}

    unauthorized_get = a2a_operation_response_from_dict(
        server.handle_task_get("task_1"),
    )
    unauthorized_cancel = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_1"),
    )
    unauthorized_create = a2a_operation_response_from_dict(
        server.handle_push_notification_config_create(
            "task_1",
            A2APushNotificationConfig(
                config_id="cfg_1",
                url="https://client.example/webhook",
            ),
        ),
    )
    authorized_create = server.handle_push_notification_config_create(
        "task_1",
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        headers=headers,
    )
    authorized_list = server.handle_push_notification_config_list(
        "task_1",
        headers=headers,
    )

    assert unauthorized_get.error is not None
    assert unauthorized_get.error.code == -32030
    assert unauthorized_cancel.error is not None
    assert unauthorized_cancel.error.code == -32030
    assert unauthorized_create.error is not None
    assert unauthorized_create.error.code == -32030
    assert authorized_create["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert authorized_list["result"]["pushNotificationConfigs"][0]["id"] == "cfg_1"


def test_a2a_operation_client_posts_message_send() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="chat", name="Chat", description="Chat."),),
    )
    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("hello"),),
        context_id="ctx_1",
    )

    response = client.send_message(
        card,
        message,
        request_id="req_1",
        timeout_seconds=7,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert response.task is not None
    assert response.task.state == "completed"
    assert response.task.messages[-1].parts[0].text == "remote ok"
    assert transport.posts == [
        (
            "https://remote.example/a2a/message:send",
            {
                "jsonrpc": "2.0",
                "id": "req_1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "role": "user",
                            "parts": [{"text": "hello"}],
                            "contextId": "ctx_1",
                        },
                },
            },
            7,
            {
                "A2A-Version": "1.0",
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            },
        ),
    ]


def test_a2a_operation_client_posts_official_jsonrpc_binding_to_declared_endpoint() -> None:
    from agentos.channels.a2a import A2AAgentInterface
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        version="1.0.0",
        supported_interfaces=(
            A2AAgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="https://remote.example/a2a/jsonrpc",
            ),
        ),
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        request_id="req_1",
    )

    assert transport.posts[0][0] == "https://remote.example/a2a/jsonrpc"
    assert transport.posts[0][1]["method"] == "SendMessage"
    assert transport.posts[0][3] == {"A2A-Version": "1.0"}


def test_a2a_operation_client_posts_message_stream() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="chat", name="Chat", description="Chat."),),
    )
    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("hello"),),
        context_id="ctx_1",
    )

    response = client.stream_message(
        card,
        message,
        request_id="req_stream",
        timeout_seconds=11,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert response.task is not None
    assert response.task.state == "completed"
    assert response.task.messages[-1].parts[0].text == "remote ok"
    assert transport.posts == [
        (
            "https://remote.example/a2a/message:stream",
            {
                "jsonrpc": "2.0",
                "id": "req_stream",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"text": "hello"}],
                        "contextId": "ctx_1",
                    },
                },
            },
            11,
            {
                "A2A-Version": "1.0",
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            },
        ),
    ]


def test_a2a_operation_client_posts_official_stream_jsonrpc_binding_to_declared_endpoint() -> None:
    from agentos.channels.a2a import A2AAgentInterface
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        version="1.0.0",
        supported_interfaces=(
            A2AAgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="https://remote.example/a2a/jsonrpc",
            ),
        ),
    )

    client.stream_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        request_id="req_stream",
    )

    assert transport.posts[0][0] == "https://remote.example/a2a/jsonrpc"
    assert transport.posts[0][1]["method"] == "SendStreamingMessage"
    assert transport.posts[0][3] == {"A2A-Version": "1.0"}


def test_a2a_operation_client_stream_message_events_posts_sse() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeSseTransport(
        (
            (
                'event: task\n'
                'data: {"task": {"id": "task_remote", "contextId": "ctx_1", '
                '"status": {"state": "submitted"}}}\n\n'
            ),
            (
                'event: task_status_update\n'
                'data: {"statusUpdate": {"taskId": "task_remote", '
                '"contextId": "ctx_1", "status": {"state": "completed"}, '
                '"metadata": {"version": "2", "final": true}}}\n\n'
            ),
        ),
    )
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="chat", name="Chat", description="Chat."),),
    )
    message = A2AMessage(
        role="user",
        parts=(A2AMessagePart.from_text("hello"),),
        context_id="ctx_1",
    )

    events = tuple(
        client.stream_message_events(
            card,
            message,
            request_id="req_stream",
            timeout_seconds=13,
            headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
        ),
    )

    assert events[0].task is not None
    assert events[0].task.task_id == "task_remote"
    assert events[1].task_event is not None
    assert events[1].task_event.final is True
    assert events[1].task_event.task.state == "completed"
    assert transport.posts == [
        (
            "https://remote.example/a2a/message:stream",
            {
                "jsonrpc": "2.0",
                "id": "req_stream",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"text": "hello"}],
                        "contextId": "ctx_1",
                    },
                },
            },
            13,
            {
                "A2A-Version": "1.0",
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            },
        ),
    ]


def test_a2a_operation_client_task_resubscribe_posts_json_rpc() -> None:
    from agentos.channels.a2a_operations import A2AOperationClient

    transport = FakeTransport(
        {
            "jsonrpc": "2.0",
            "id": "req_subscribe",
            "result": {
                "statusUpdate": {
                    "taskId": "task_remote",
                    "contextId": "ctx_1",
                    "status": {"state": "completed"},
                    "metadata": {
                        "version": 5,
                        "final": True,
                    },
                    "final": True,
                },
            },
        },
    )
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="chat", name="Chat", description="Chat."),),
    )

    response = client.task_resubscribe(
        card,
        "task_remote",
        after_event_id=4,
        request_id="req_subscribe",
        timeout_seconds=17,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert response.task_event is not None
    assert response.task_event.event_id == "5"
    assert response.task_event.final is True
    assert response.task_event.task.task_id == "task_remote"
    assert transport.posts == [
        (
            "https://remote.example/a2a/tasks/task_remote:subscribe",
            {
                "jsonrpc": "2.0",
                "id": "req_subscribe",
                "method": "SubscribeToTask",
                "params": {"id": "task_remote", "afterEventId": 4},
            },
            17,
            {
                "A2A-Version": "1.0",
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            },
        ),
    ]


def test_a2a_operation_client_posts_official_subscribe_jsonrpc_binding_to_declared_endpoint() -> None:
    from agentos.channels.a2a import A2AAgentInterface
    from agentos.channels.a2a_operations import A2AOperationClient

    transport = FakeTransport(
        {
            "jsonrpc": "2.0",
            "id": "req_subscribe",
            "result": {
                "statusUpdate": {
                    "taskId": "task_remote",
                    "status": {"state": "working"},
                    "metadata": {"version": 7},
                },
            },
        },
    )
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        version="1.0.0",
        supported_interfaces=(
            A2AAgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="https://remote.example/a2a/jsonrpc",
            ),
        ),
    )

    client.task_resubscribe(
        card,
        "task_remote",
        after_event_id=6,
        request_id="req_subscribe",
    )

    assert transport.posts[0][0] == "https://remote.example/a2a/jsonrpc"
    assert transport.posts[0][1]["method"] == "SubscribeToTask"
    assert transport.posts[0][3] == {"A2A-Version": "1.0"}


def test_a2a_operation_client_sends_default_protocol_version_header() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
    )
    client.get_task(card, "task_remote")
    client.cancel_task(card, "task_remote")

    assert transport.posts[0][3] == {"A2A-Version": "1.0"}
    assert transport.posts[1][3] == {"A2A-Version": "1.0"}
    assert transport.posts[2][3] == {"A2A-Version": "1.0"}


def test_a2a_operation_client_allows_protocol_version_override() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    transport = FakeTransport()
    client = A2AOperationClient(transport=transport)
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        headers={"A2A-Version": "0.3"},
    )

    assert transport.posts[0][3] == {"A2A-Version": "0.3"}


def test_a2a_operation_client_sends_supported_extension_header() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    supported_uri = "https://extensions.example/trace-artifacts/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=(supported_uri,),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(
                A2AAgentExtension(uri=supported_uri),
                A2AAgentExtension(uri="https://extensions.example/other/v1"),
            ),
        ),
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        timeout_seconds=3,
    )

    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "A2A-Extensions": supported_uri,
    }


def test_a2a_operation_client_stream_message_sends_supported_extension_header() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    supported_uri = "https://extensions.example/stream-trace/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=(supported_uri,),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=supported_uri),),
        ),
    )

    client.stream_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        timeout_seconds=3,
    )

    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "A2A-Extensions": supported_uri,
    }


def test_a2a_operation_client_rejects_required_unsupported_extension_before_network() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationError,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    required_uri = "https://extensions.example/required/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=("https://extensions.example/known/v1",),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=required_uri, required=True),),
        ),
    )

    with pytest.raises(
        A2AExtensionNegotiationError,
        match="requires unsupported extensions",
    ):
        client.send_message(
            card,
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.posts == []


def test_a2a_operation_client_stream_message_rejects_required_unsupported_extension_before_network() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationError,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    required_uri = "https://extensions.example/stream-required/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=("https://extensions.example/known/v1",),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=required_uri, required=True),),
        ),
    )

    with pytest.raises(
        A2AExtensionNegotiationError,
        match="requires unsupported extensions",
    ):
        client.stream_message(
            card,
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.posts == []


def test_a2a_operation_client_task_resubscribe_rejects_required_unsupported_extension_before_network() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AExtensionNegotiationError,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    required_uri = "https://extensions.example/task-resubscribe-required/v1"
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=("https://extensions.example/known/v1",),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=required_uri, required=True),),
        ),
    )

    with pytest.raises(
        A2AExtensionNegotiationError,
        match="requires unsupported extensions",
    ):
        client.task_resubscribe(card, "task_remote", after_event_id=4)

    assert transport.posts == []


def test_a2a_stream_lifecycle_profile_reports_missing_components() -> None:
    from agentos.channels.a2a_operations import A2AStreamLifecycleDeploymentProfile

    profile = A2AStreamLifecycleDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "A2AStreamLifecycleDeploymentProfile"
    assert metadata["probe_name"] == "a2a_stream_lifecycle"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "reconnect_policy",
        "durable_cursor_store",
        "fanout_broker",
        "backpressure_policy",
        "stream_supervision",
    }
    assert "message/stream operation boundary" in metadata["sdk_owned"]
    assert "durable cursor storage" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_a2a_stream_lifecycle_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.channels.a2a_operations import A2AStreamLifecycleDeploymentProfile

    profile = A2AStreamLifecycleDeploymentProfile(
        configured_components=(
            "reconnect_policy",
            "durable_cursor_store",
            "fanout_broker",
            "backpressure_policy",
            "stream_supervision",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_a2a_stream_lifecycle_profile_rejects_empty_names() -> None:
    from agentos.channels.a2a_operations import A2AStreamLifecycleDeploymentProfile

    with pytest.raises(ValueError, match="probe_name"):
        A2AStreamLifecycleDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        A2AStreamLifecycleDeploymentProfile(configured_components=("",))


def test_a2a_operation_client_stream_message_events_rejects_required_unsupported_extension_before_network() -> None:
    from agentos.channels.a2a import A2AAgentCapabilities, A2AAgentExtension
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AExtensionNegotiationError,
        A2AExtensionNegotiationPolicy,
        A2AOperationClient,
    )

    required_uri = "https://extensions.example/stream-events-required/v1"
    transport = FakeSseTransport(())
    client = A2AOperationClient(
        transport=transport,
        extension_negotiation_policy=A2AExtensionNegotiationPolicy(
            supported_extensions=("https://extensions.example/known/v1",),
        ),
    )
    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
        capabilities=A2AAgentCapabilities(
            extensions=(A2AAgentExtension(uri=required_uri, required=True),),
        ),
    )

    with pytest.raises(
        A2AExtensionNegotiationError,
        match="requires unsupported extensions",
    ):
        client.stream_message_events(
            card,
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        )

    assert transport.posts == []


def test_a2a_operation_server_rejects_unsupported_protocol_version() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        supported_protocol_versions=("1.0",),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    response = a2a_operation_response_from_dict(
        asyncio.run(
            server.handle_operation(payload, headers={"A2A-Version": "0.5"}),
        ),
    )

    assert response.request_id == "req_1"
    assert response.error is not None
    assert response.error.code == -32009
    assert response.error.message == "protocol version not supported"
    assert response.error.data == {
        "type": "https://a2a-protocol.org/errors/version-not-supported",
        "title": "Protocol Version Not Supported",
        "status": 400,
        "detail": "The requested A2A protocol version 0.5 is not supported by this agent",
        "requestedVersion": "0.5",
        "supportedVersions": ["1.0"],
    }


def test_a2a_operation_server_treats_empty_protocol_version_as_legacy_03() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        A2AOperationServer,
        AgentA2AOperationRunner,
        a2a_operation_request_to_dict,
        a2a_operation_response_from_dict,
    )

    missing_server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("legacy ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        supported_protocol_versions=("0.3",),
    )
    empty_server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("legacy ok")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
        supported_protocol_versions=("0.3",),
    )
    payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
            request_id="req_1",
        ),
    )

    missing = a2a_operation_response_from_dict(
        asyncio.run(missing_server.handle_operation(payload)),
    )
    empty = a2a_operation_response_from_dict(
        asyncio.run(
            empty_server.handle_operation(payload, headers={"A2A-Version": ""}),
        ),
    )

    assert missing.error is None
    assert missing.task is not None
    assert missing.task.messages[-1].parts[0].text == "legacy ok"
    assert empty.error is None
    assert empty.task is not None


def test_a2a_task_from_task_record_maps_internal_statuses() -> None:
    from agentos.channels.a2a_operations import (
        a2a_state_from_task_status,
        a2a_task_from_task_record,
    )

    assert a2a_state_from_task_status("queued") == "submitted"
    assert a2a_state_from_task_status("running") == "working"
    assert a2a_state_from_task_status("completed") == "completed"
    assert a2a_state_from_task_status("failed") == "failed"
    assert a2a_state_from_task_status("cancelled") == "canceled"
    assert a2a_state_from_task_status("timeout") == "failed"

    task = a2a_task_from_task_record(
        task_record(
            status="completed",
            result=TaskResult(
                task_id="task_1",
                status="completed",
                summary="done",
                artifacts={"path": "artifact.txt"},
            ),
        ),
    )

    assert task.task_id == "task_1"
    assert task.context_id == "parent"
    assert task.state == "completed"
    assert task.messages[-1].role == "agent"
    assert task.messages[-1].parts[0].text == "done"
    assert task.artifacts[0].artifact_id == "task_1-result"
    assert task.artifacts[0].parts[0].data == {"path": "artifact.txt"}
    assert task.metadata["internalStatus"] == "completed"

    cancelling = a2a_task_from_task_record(
        task_record(status="running", cancel_requested_at=2.5),
    )
    assert cancelling.state == "working"
    assert cancelling.metadata["cancelRequested"] is True


def test_task_store_a2a_lifecycle_runner_gets_and_cancels_task() -> None:
    from agentos.channels.a2a_operations import TaskStoreA2ATaskLifecycleRunner

    store = TaskTable()
    store.create(task_record(task_id="task_cancel", status="queued"))
    runner = TaskStoreA2ATaskLifecycleRunner(store, clock=lambda: 3.0)

    before = runner.get_task("task_cancel")
    cancelled = runner.cancel_task("task_cancel")

    assert before is not None
    assert before.state == "submitted"
    assert cancelled is not None
    assert cancelled.state == "canceled"
    record = store.get("task_cancel")
    assert record is not None
    assert record.status == "cancelled"


def test_task_store_a2a_lifecycle_runner_subscribes_to_task_updates() -> None:
    from agentos.channels.a2a_operations import (
        A2ATaskSubscriptionEvent,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_task_subscription_event_from_dict,
        a2a_task_subscription_event_to_dict,
    )

    store = TaskTable()
    store.create(
        TaskRecord(
            task_id="task_stream",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_stream", instruction="do work"),
            status="queued",
            created_at=1.0,
            deadline_at=10.0,
        ),
    )
    runner = TaskStoreA2ATaskLifecycleRunner(store)

    initial = runner.next_task_update("task_stream", after_version=None)
    assert initial is not None
    assert initial.event_id == "0"
    assert initial.task.state == "submitted"
    assert initial.final is False

    assert runner.next_task_update("task_stream", after_version=0) is None

    store.mark_running("task_stream", now=2.0)
    running = runner.next_task_update("task_stream", after_version=0)
    assert running is not None
    assert running.event_id == "1"
    assert running.task.state == "working"
    assert running.final is False

    store.mark_completed(
        "task_stream",
        TaskResult(
            task_id="task_stream",
            status="completed",
            summary="done",
        ),
        now=3.0,
        worker_id=None,
        attempt=None,
    )
    completed = runner.next_task_update("task_stream", after_version=1)
    assert completed == A2ATaskSubscriptionEvent(
        event_id="2",
        task=completed.task,  # type: ignore[union-attr]
        final=True,
    )
    assert (
        a2a_task_subscription_event_from_dict(
            a2a_task_subscription_event_to_dict(completed),
        )
        == completed
    )


def test_a2a_push_notification_config_round_trip_and_store_validation() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationAuthentication,
        A2APushNotificationConfig,
        A2APushNotificationConfigError,
        InMemoryA2APushNotificationConfigStore,
        a2a_push_notification_config_from_dict,
        a2a_push_notification_config_to_dict,
    )

    config = A2APushNotificationConfig(
        url="https://client.example/webhook",
        authentication=A2APushNotificationAuthentication(
            schemes=("Bearer",),
            credentials="token-1",
        ),
        config_id="cfg_1",
        token="opaque",
    )
    payload = a2a_push_notification_config_to_dict(config)

    assert payload == {
        "id": "cfg_1",
        "url": "https://client.example/webhook",
        "token": "opaque",
        "authentication": {
            "schemes": ["Bearer"],
            "credentials": "token-1",
        },
    }
    assert a2a_push_notification_config_from_dict(payload) == config

    store = InMemoryA2APushNotificationConfigStore()
    created = store.create("task_1", config)
    assert created == config
    assert store.get("task_1", "cfg_1") == config
    assert store.list("task_1") == (config,)
    assert store.delete("task_1", "cfg_1") is True
    assert store.delete("task_1", "cfg_1") is False

    try:
        store.create(
            "task_1",
            A2APushNotificationConfig(url="http://169.254.169.254/latest"),
        )
    except A2APushNotificationConfigError as error:
        assert "https webhook url is required" in str(error)
    else:
        raise AssertionError("non-HTTPS webhook URL should be rejected")


def test_a2a_push_notification_host_allow_list_url_policy_accepts_exact_and_suffix() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        HostAllowListA2APushNotificationUrlPolicy,
    )

    policy = HostAllowListA2APushNotificationUrlPolicy(
        allowed_hosts=("client.example",),
        allowed_domain_suffixes=(".trusted.example",),
    )

    policy.validate(
        A2APushNotificationConfig(url="https://client.example/webhook"),
    )
    policy.validate(
        A2APushNotificationConfig(url="https://team.trusted.example/webhook"),
    )
    policy.validate(
        A2APushNotificationConfig(url="https://trusted.example/webhook"),
    )


def test_a2a_push_notification_host_allow_list_url_policy_rejects_unlisted_hosts() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationConfigError,
        HostAllowListA2APushNotificationUrlPolicy,
    )

    policy = HostAllowListA2APushNotificationUrlPolicy(
        allowed_domain_suffixes=(".trusted.example",),
    )

    with pytest.raises(A2APushNotificationConfigError) as disallowed:
        policy.validate(
            A2APushNotificationConfig(url="https://other.example/webhook"),
        )
    with pytest.raises(A2APushNotificationConfigError) as tricky_suffix:
        policy.validate(
            A2APushNotificationConfig(
                url="https://tenant.trusted.example.evil.com/webhook",
            ),
        )

    assert "allowed webhook hostname is required" in str(disallowed.value)
    assert "allowed webhook hostname is required" in str(tricky_suffix.value)


def test_a2a_push_notification_config_store_enforces_url_policy() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationConfigError,
        HostAllowListA2APushNotificationUrlPolicy,
        InMemoryA2APushNotificationConfigStore,
    )

    store = InMemoryA2APushNotificationConfigStore(
        url_policy=HostAllowListA2APushNotificationUrlPolicy(
            allowed_hosts=("client.example",),
        ),
    )
    allowed = A2APushNotificationConfig(
        config_id="cfg_allowed",
        url="https://client.example/webhook",
    )

    created = store.create("task_1", allowed)
    with pytest.raises(A2APushNotificationConfigError):
        store.create(
            "task_1",
            A2APushNotificationConfig(
                config_id="cfg_blocked",
                url="https://other.example/webhook",
            ),
        )

    assert created == allowed
    assert store.list("task_1") == (allowed,)


def test_a2a_operation_server_manages_push_notification_configs() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        A2APushNotificationConfig,
        InMemoryA2APushNotificationConfigStore,
    )

    store = InMemoryA2APushNotificationConfigStore()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        push_notification_configs=store,
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    config = A2APushNotificationConfig(
        url="https://client.example/webhook",
        config_id="cfg_1",
    )

    created = server.handle_push_notification_config_create("task_1", config)
    fetched = server.handle_push_notification_config_get("task_1", "cfg_1")
    listed = server.handle_push_notification_config_list("task_1")
    deleted = server.handle_push_notification_config_delete("task_1", "cfg_1")

    assert created["result"]["pushNotificationConfig"]["id"] == "cfg_1"
    assert fetched["result"]["pushNotificationConfig"]["url"] == (
        "https://client.example/webhook"
    )
    assert listed["result"]["pushNotificationConfigs"] == [
        {
            "id": "cfg_1",
            "url": "https://client.example/webhook",
        },
    ]
    assert deleted["result"] == {}
    assert store.list("task_1") == ()


def test_a2a_operation_server_redacts_push_notification_config_response_secrets() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        A2APushNotificationAuthentication,
        A2APushNotificationConfig,
        AgentA2AOperationRunner,
        InMemoryA2APushNotificationConfigStore,
    )

    store = InMemoryA2APushNotificationConfigStore()
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        push_notification_configs=store,
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    config = A2APushNotificationConfig(
        url="https://client.example/webhook",
        authentication=A2APushNotificationAuthentication(
            schemes=("Bearer",),
            credentials="secret-credential",
        ),
        config_id="cfg_1",
        token="opaque-token",
    )

    created = server.handle_push_notification_config_create("task_1", config)
    fetched = server.handle_push_notification_config_get("task_1", "cfg_1")
    listed = server.handle_push_notification_config_list("task_1")

    assert created["result"]["pushNotificationConfig"] == {
        "id": "cfg_1",
        "url": "https://client.example/webhook",
        "authentication": {"schemes": ["Bearer"]},
    }
    assert fetched == created
    assert listed["result"]["pushNotificationConfigs"] == [
        {
            "id": "cfg_1",
            "url": "https://client.example/webhook",
            "authentication": {"schemes": ["Bearer"]},
        },
    ]
    assert store.get("task_1", "cfg_1") == config


def test_a2a_operation_server_returns_push_config_errors() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        A2APushNotificationConfig,
        a2a_operation_response_from_dict,
    )

    missing_store = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )
    unsupported = a2a_operation_response_from_dict(
        missing_store.handle_push_notification_config_create(
            "task_1",
            A2APushNotificationConfig(
                url="https://client.example/webhook",
                config_id="cfg_1",
            ),
        ),
    )

    assert unsupported.error is not None
    assert unsupported.error.code == -32010



def test_a2a_operation_server_handles_task_get_and_cancel() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_response_from_dict,
    )

    store = TaskTable()
    store.create(task_record(task_id="task_1", status="queued"))
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store, clock=lambda: 4.0),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    found = a2a_operation_response_from_dict(server.handle_task_get("task_1"))
    cancelled = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_1"),
    )

    assert found.task is not None
    assert found.task.state == "submitted"
    assert cancelled.task is not None
    assert cancelled.task.state == "canceled"


def test_a2a_operation_server_returns_task_lifecycle_errors() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationServer,
        AgentA2AOperationRunner,
        TaskStoreA2ATaskLifecycleRunner,
        a2a_operation_response_from_dict,
    )

    store = TaskTable()
    store.create(
        task_record(
            task_id="task_done",
            status="completed",
            result=TaskResult(
                task_id="task_done",
                status="completed",
                summary="done",
            ),
        ),
    )
    server = A2AOperationServer(
        AgentA2AOperationRunner(build_agent_with_response("unused")),
        task_lifecycle=TaskStoreA2ATaskLifecycleRunner(store, clock=time.time),
        inbound_auth_policy=AllowAllA2AInboundAuthPolicy(),
    )

    missing = a2a_operation_response_from_dict(server.handle_task_get("missing"))
    not_cancelable = a2a_operation_response_from_dict(
        server.handle_task_cancel("task_done"),
    )

    assert missing.error is not None
    assert missing.error.code == -32001
    assert not_cancelable.error is not None
    assert not_cancelable.error.code == -32002


def test_a2a_operation_client_gets_and_cancels_task() -> None:
    from agentos.channels.a2a_operations import A2AOperationClient

    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )
    get_transport = FakeTransport()
    get_client = A2AOperationClient(transport=get_transport)
    cancel_transport = FakeTransport()
    cancel_client = A2AOperationClient(transport=cancel_transport)

    found = get_client.get_task(card, "task_remote", timeout_seconds=3)
    cancelled = cancel_client.cancel_task(
        card,
        "task_remote",
        timeout_seconds=5,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert found.task is not None
    assert found.task.task_id == "task_remote"
    assert cancelled.task is not None
    assert get_transport.posts == [
        (
            "https://remote.example/a2a/tasks/task_remote",
            {"method": "GET"},
            3,
            {"A2A-Version": "1.0"},
        ),
    ]
    assert cancel_transport.posts[0] == (
        "https://remote.example/a2a/tasks/task_remote:cancel",
        {"jsonrpc": "2.0", "method": "tasks/cancel", "params": {"id": "task_remote"}},
        5,
        {
            "A2A-Version": "1.0",
            "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        },
    )


def test_a2a_operation_client_injects_auth_headers_for_peer_calls() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        auth_provider=StaticBearerA2AAuthProvider("token-for-remote"),
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        timeout_seconds=1,
    )
    client.get_task(card, "task_remote", timeout_seconds=2)
    client.cancel_task(
        card,
        "task_remote",
        timeout_seconds=3,
        headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "Authorization": "Bearer token-for-remote",
    }
    assert transport.posts[1][3] == {
        "A2A-Version": "1.0",
        "Authorization": "Bearer token-for-remote",
    }
    assert transport.posts[2][3] == {
        "A2A-Version": "1.0",
        "Authorization": "Bearer token-for-remote",
        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
    }


def test_a2a_operation_client_explicit_headers_override_auth_provider() -> None:
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationClient,
    )

    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )
    transport = FakeTransport()
    client = A2AOperationClient(
        transport=transport,
        auth_provider=StaticBearerA2AAuthProvider("token-for-remote"),
    )

    client.send_message(
        card,
        A2AMessage(role="user", parts=(A2AMessagePart.from_text("hello"),)),
        headers={"Authorization": "Bearer caller-token"},
    )

    assert transport.posts[0][3] == {
        "A2A-Version": "1.0",
        "Authorization": "Bearer caller-token",
    }


def test_a2a_operation_client_manages_push_notification_configs() -> None:
    from agentos.channels.a2a_operations import (
        A2AOperationClient,
        A2APushNotificationConfig,
    )

    class PushTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.gets: list[
                tuple[str, float, dict[str, str] | None]
            ] = []
            self.deletes: list[
                tuple[str, float, dict[str, str] | None]
            ] = []

        def post_json(
            self,
            url: str,
            payload: dict[str, object],
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.posts.append((url, payload, timeout_seconds, headers))
            return {"result": {"pushNotificationConfig": payload}}

        def get_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.gets.append((url, timeout_seconds, headers))
            if url.endswith("/cfg_1"):
                return {
                    "result": {
                        "pushNotificationConfig": {
                            "id": "cfg_1",
                            "url": "https://client.example/webhook",
                        },
                    },
                }
            return {
                "result": {
                    "pushNotificationConfigs": [
                        {
                            "id": "cfg_1",
                            "url": "https://client.example/webhook",
                        },
                    ],
                },
            }

        def delete_json(
            self,
            url: str,
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.deletes.append((url, timeout_seconds, headers))
            return {"result": {}}

    card = A2AAgentCard(
        name="Remote",
        description="Remote agent.",
        url="https://remote.example/a2a",
        version="1.0.0",
    )
    transport = PushTransport()
    client = A2AOperationClient(
        transport=transport,
        auth_provider=StaticBearerA2AAuthProvider("token-for-remote"),
    )

    created = client.create_push_notification_config(
        card,
        "task_1",
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        timeout_seconds=1,
    )
    fetched = client.get_push_notification_config(
        card,
        "task_1",
        "cfg_1",
        timeout_seconds=2,
    )
    listed = client.list_push_notification_configs(
        card,
        "task_1",
        timeout_seconds=3,
    )
    client.delete_push_notification_config(
        card,
        "task_1",
        "cfg_1",
        timeout_seconds=4,
    )

    assert created.url == "https://client.example/webhook"
    assert fetched.config_id == "cfg_1"
    assert listed == (
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
    )
    assert transport.posts[0] == (
        "https://remote.example/a2a/tasks/task_1/pushNotificationConfigs",
        {"id": "cfg_1", "url": "https://client.example/webhook"},
        1,
        {
            "A2A-Version": "1.0",
            "Authorization": "Bearer token-for-remote",
        },
    )
    assert transport.gets == [
        (
            "https://remote.example/a2a/tasks/task_1/pushNotificationConfigs/cfg_1",
            2,
            {
                "A2A-Version": "1.0",
                "Authorization": "Bearer token-for-remote",
            },
        ),
        (
            "https://remote.example/a2a/tasks/task_1/pushNotificationConfigs",
            3,
            {
                "A2A-Version": "1.0",
                "Authorization": "Bearer token-for-remote",
            },
        ),
    ]
    assert transport.deletes == [
        (
            "https://remote.example/a2a/tasks/task_1/pushNotificationConfigs/cfg_1",
            4,
            {
                "A2A-Version": "1.0",
                "Authorization": "Bearer token-for-remote",
            },
        ),
    ]


def test_a2a_push_notification_dispatcher_posts_task_updates_with_auth() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationAuthentication,
        A2APushNotificationConfig,
        A2APushNotificationDelivery,
        A2APushNotificationDispatcher,
        A2ATask,
        A2ATaskSubscriptionEvent,
    )

    class RecordingWebhookTransport:
        def __init__(self) -> None:
            self.posts: list[
                tuple[str, dict[str, object], float, dict[str, str] | None]
            ] = []

        def post_json(
            self,
            url: str,
            payload: dict[str, object],
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.posts.append((url, payload, timeout_seconds, headers))
            return {"status": "accepted"}

    transport = RecordingWebhookTransport()
    dispatcher = A2APushNotificationDispatcher(
        transport=transport,
        timeout_seconds=12,
    )
    config = A2APushNotificationConfig(
        config_id="cfg_1",
        url="https://client.example/webhook",
        authentication=A2APushNotificationAuthentication(
            schemes=("Bearer",),
            credentials="secret-token",
        ),
    )
    event = A2ATaskSubscriptionEvent(
        event_id="7",
        task=A2ATask(task_id="task_1", context_id="ctx_1", state="completed"),
        final=True,
    )

    delivery = dispatcher.deliver(config, event)

    assert delivery == A2APushNotificationDelivery(
        config_id="cfg_1",
        url="https://client.example/webhook",
        status="delivered",
        response={"status": "accepted"},
    )
    assert "secret-token" not in repr(delivery)
    assert transport.posts == [
        (
            "https://client.example/webhook",
            {
                "statusUpdate": {
                    "taskId": "task_1",
                    "contextId": "ctx_1",
                    "status": {"state": "completed"},
                    "metadata": {
                        "version": "7",
                        "final": True,
                    },
                },
            },
            12,
            {"Authorization": "Bearer secret-token"},
        ),
    ]


def test_a2a_push_notification_dispatcher_reports_delivery_errors() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDelivery,
        A2APushNotificationDispatcher,
        A2ATask,
        A2ATaskSubscriptionEvent,
    )

    class FailingWebhookTransport:
        def post_json(
            self,
            url: str,
            payload: dict[str, object],
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            raise RuntimeError("connection refused")

    dispatcher = A2APushNotificationDispatcher(transport=FailingWebhookTransport())
    event = A2ATaskSubscriptionEvent(
        event_id="1",
        task=A2ATask(task_id="task_1", context_id=None, state="failed"),
        final=True,
    )

    delivery = dispatcher.deliver(
        A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
            token="do-not-leak",
        ),
        event,
    )

    assert delivery == A2APushNotificationDelivery(
        config_id="cfg_1",
        url="https://client.example/webhook",
        status="failed",
        error="connection refused",
    )
    assert "do-not-leak" not in repr(delivery)


def test_a2a_push_notification_dispatcher_enforces_url_policy_before_transport() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDispatcher,
        A2ATask,
        A2ATaskSubscriptionEvent,
        HostAllowListA2APushNotificationUrlPolicy,
    )

    class RecordingWebhookTransport:
        def __init__(self) -> None:
            self.posts: list[
                tuple[str, dict[str, object], float, dict[str, str] | None]
            ] = []

        def post_json(
            self,
            url: str,
            payload: dict[str, object],
            timeout_seconds: float,
            *,
            headers: dict[str, str] | None = None,
        ) -> dict[str, object]:
            self.posts.append((url, payload, timeout_seconds, headers))
            return {"status": "accepted"}

    transport = RecordingWebhookTransport()
    dispatcher = A2APushNotificationDispatcher(
        transport=transport,
        url_policy=HostAllowListA2APushNotificationUrlPolicy(
            allowed_hosts=("client.example",),
        ),
    )
    event = A2ATaskSubscriptionEvent(
        event_id="1",
        task=A2ATask(task_id="task_1", context_id=None, state="failed"),
        final=True,
    )

    delivery = dispatcher.deliver(
        A2APushNotificationConfig(
            config_id="cfg_blocked",
            url="https://other.example/webhook",
        ),
        event,
    )

    assert delivery.status == "failed"
    assert delivery.error is not None
    assert "allowed webhook hostname is required" in delivery.error
    assert transport.posts == []


def test_a2a_push_notification_delivery_store_claims_due_records() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDeliveryRecord,
        A2ATask,
        A2ATaskSubscriptionEvent,
        InMemoryA2APushNotificationDeliveryStore,
    )

    store = InMemoryA2APushNotificationDeliveryStore()
    record = A2APushNotificationDeliveryRecord(
        delivery_id="delivery_1",
        task_id="task_1",
        config=A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        event=A2ATaskSubscriptionEvent(
            event_id="7",
            task=A2ATask(task_id="task_1", context_id="ctx_1", state="working"),
        ),
        created_at=1.0,
        next_run_at=5.0,
    )

    stored = store.enqueue(record)
    early = store.claim_due(
        now=4.9,
        worker_id="worker-a",
        lease_seconds=30,
        limit=10,
    )
    claimed = store.claim_due(
        now=5.0,
        worker_id="worker-a",
        lease_seconds=30,
        limit=10,
    )

    assert stored == record
    assert early == ()
    assert len(claimed) == 1
    assert claimed[0].delivery_id == "delivery_1"
    assert claimed[0].status == "running"
    assert claimed[0].worker_id == "worker-a"
    assert claimed[0].lease_expires_at == 35.0
    assert store.list_records(status="running") == claimed


def test_a2a_push_notification_delivery_worker_marks_delivered() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDelivery,
        A2APushNotificationDeliveryRecord,
        A2APushNotificationDeliveryWorker,
        A2APushNotificationRetryPolicy,
        A2ATask,
        A2ATaskSubscriptionEvent,
        InMemoryA2APushNotificationDeliveryStore,
    )

    class SuccessfulDispatcher:
        def __init__(self) -> None:
            self.delivered: list[tuple[A2APushNotificationConfig, str]] = []

        def deliver(
            self,
            config: A2APushNotificationConfig,
            event: A2ATaskSubscriptionEvent,
        ) -> A2APushNotificationDelivery:
            self.delivered.append((config, event.event_id))
            return A2APushNotificationDelivery(
                config_id=config.config_id,
                url=config.url,
                status="delivered",
                response={"status": "accepted"},
            )

    store = InMemoryA2APushNotificationDeliveryStore()
    event = A2ATaskSubscriptionEvent(
        event_id="8",
        task=A2ATask(task_id="task_1", context_id="ctx_1", state="completed"),
        final=True,
    )
    config = A2APushNotificationConfig(
        config_id="cfg_1",
        url="https://client.example/webhook",
    )
    store.enqueue(
        A2APushNotificationDeliveryRecord(
            delivery_id="delivery_1",
            task_id="task_1",
            config=config,
            event=event,
            created_at=1.0,
            next_run_at=5.0,
        ),
    )
    dispatcher = SuccessfulDispatcher()
    worker = A2APushNotificationDeliveryWorker(
        store,
        dispatcher,
        retry_policy=A2APushNotificationRetryPolicy(max_attempts=3),
    )

    results = worker.run_pending(now=5.0, worker_id="worker-a", limit=10)
    stored = store.get("delivery_1")

    assert len(results) == 1
    assert results[0].status == "delivered"
    assert results[0].delivered_at == 5.0
    assert results[0].response == {"status": "accepted"}
    assert stored == results[0]
    assert dispatcher.delivered == [(config, "8")]


def test_a2a_push_notification_delivery_worker_schedules_retry_with_backoff() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDelivery,
        A2APushNotificationDeliveryRecord,
        A2APushNotificationDeliveryWorker,
        A2APushNotificationRetryPolicy,
        A2ATask,
        A2ATaskSubscriptionEvent,
        InMemoryA2APushNotificationDeliveryStore,
    )

    class FailingDispatcher:
        def deliver(
            self,
            config: A2APushNotificationConfig,
            event: A2ATaskSubscriptionEvent,
        ) -> A2APushNotificationDelivery:
            return A2APushNotificationDelivery(
                config_id=config.config_id,
                url=config.url,
                status="failed",
                error="temporary outage",
            )

    store = InMemoryA2APushNotificationDeliveryStore()
    store.enqueue(
        A2APushNotificationDeliveryRecord(
            delivery_id="delivery_1",
            task_id="task_1",
            config=A2APushNotificationConfig(
                config_id="cfg_1",
                url="https://client.example/webhook",
            ),
            event=A2ATaskSubscriptionEvent(
                event_id="9",
                task=A2ATask(task_id="task_1", context_id=None, state="failed"),
                final=True,
            ),
            created_at=1.0,
            next_run_at=5.0,
        ),
    )
    worker = A2APushNotificationDeliveryWorker(
        store,
        FailingDispatcher(),
        retry_policy=A2APushNotificationRetryPolicy(
            max_attempts=3,
            backoff_seconds=10,
            backoff_multiplier=2,
        ),
    )

    results = worker.run_pending(now=5.0, worker_id="worker-a", limit=10)
    skipped = worker.run_pending(now=14.0, worker_id="worker-a", limit=10)

    assert len(results) == 1
    assert results[0].status == "retry_scheduled"
    assert results[0].attempts == 1
    assert results[0].next_run_at == 15.0
    assert results[0].last_error == "temporary outage"
    assert skipped == ()


def test_a2a_push_notification_delivery_worker_dead_letters_after_max_attempts() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDelivery,
        A2APushNotificationDeliveryRecord,
        A2APushNotificationDeliveryWorker,
        A2APushNotificationRetryPolicy,
        A2ATask,
        A2ATaskSubscriptionEvent,
        InMemoryA2APushNotificationDeliveryStore,
    )

    class FailingDispatcher:
        def deliver(
            self,
            config: A2APushNotificationConfig,
            event: A2ATaskSubscriptionEvent,
        ) -> A2APushNotificationDelivery:
            return A2APushNotificationDelivery(
                config_id=config.config_id,
                url=config.url,
                status="failed",
                error="still failing",
            )

    store = InMemoryA2APushNotificationDeliveryStore()
    store.enqueue(
        A2APushNotificationDeliveryRecord(
            delivery_id="delivery_1",
            task_id="task_1",
            config=A2APushNotificationConfig(
                config_id="cfg_1",
                url="https://client.example/webhook",
            ),
            event=A2ATaskSubscriptionEvent(
                event_id="10",
                task=A2ATask(task_id="task_1", context_id=None, state="failed"),
                final=True,
            ),
            created_at=1.0,
            next_run_at=5.0,
        ),
    )
    worker = A2APushNotificationDeliveryWorker(
        store,
        FailingDispatcher(),
        retry_policy=A2APushNotificationRetryPolicy(
            max_attempts=2,
            backoff_seconds=10,
        ),
    )

    first = worker.run_pending(now=5.0, worker_id="worker-a", limit=10)
    second = worker.run_pending(now=15.0, worker_id="worker-a", limit=10)

    assert first[0].status == "retry_scheduled"
    assert second[0].status == "dead_letter"
    assert second[0].attempts == 2
    assert second[0].dead_lettered_at == 15.0
    assert store.list_records(status="dead_letter") == second


def test_a2a_push_notification_delivery_records_redact_config_secrets() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationAuthentication,
        A2APushNotificationConfig,
        A2APushNotificationDeliveryRecord,
        A2ATask,
        A2ATaskSubscriptionEvent,
    )

    record = A2APushNotificationDeliveryRecord(
        delivery_id="delivery_1",
        task_id="task_1",
        config=A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
            token="opaque-token",
            authentication=A2APushNotificationAuthentication(
                schemes=("Bearer",),
                credentials="secret-token",
            ),
        ),
        event=A2ATaskSubscriptionEvent(
            event_id="11",
            task=A2ATask(task_id="task_1", context_id=None, state="completed"),
        ),
        created_at=1.0,
        next_run_at=1.0,
        last_error="temporary failure with opaque-token and secret-token",
    )

    rendered = repr(record)

    assert "opaque-token" not in rendered
    assert "secret-token" not in rendered
    assert "temporary failure with <redacted> and <redacted>" in rendered


def test_a2a_push_notification_delivery_record_round_trip_preserves_event_id() -> None:
    from agentos.channels.a2a_operations import (
        A2APushNotificationConfig,
        A2APushNotificationDeliveryRecord,
        A2ATask,
        A2ATaskSubscriptionEvent,
        a2a_push_notification_delivery_record_from_dict,
        a2a_push_notification_delivery_record_to_dict,
    )

    record = A2APushNotificationDeliveryRecord(
        delivery_id="delivery_1",
        task_id="task_1",
        config=A2APushNotificationConfig(
            config_id="cfg_1",
            url="https://client.example/webhook",
        ),
        event=A2ATaskSubscriptionEvent(
            event_id="event_77",
            task=A2ATask(task_id="task_1", context_id="ctx_1", state="completed"),
            final=True,
        ),
        created_at=1.0,
        next_run_at=2.0,
    )

    payload = a2a_push_notification_delivery_record_to_dict(record)

    assert payload["event"]["id"] == "event_77"
    assert a2a_push_notification_delivery_record_from_dict(payload) == record
