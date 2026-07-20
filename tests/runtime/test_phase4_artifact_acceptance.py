import asyncio

import pytest

from agentos import Agent, AgentBuilder
from agentos.artifacts import ArtifactRecord
from agentos.capabilities import RegisteredTool, SideEffectPolicy, WaitRequest
from agentos.providers import (
    FakeProvider,
    ImagePart,
    ProviderInputItem,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    TextPart,
)
from agentos.runtime import AgentWaiting, UserTurnInput, WaitReason


_ARTIFACT_DATA = b"\x89PNG\r\nphase-4-drawing"
_FILENAME = "drawing.png"
_MEDIA_TYPE = "image/png"


def test_artifact_complete_e2e_reloads_on_demand_and_cleans_mount() -> None:
    async def scenario() -> None:
        provider = FakeProvider(
            [
                "首次分析完成。",
            ]
        )
        agent = AgentBuilder().provider(provider).build(
            session_id="session_artifact_e2e"
        )
        record = await agent.artifacts.upload(
            data=_ARTIFACT_DATA,
            filename=_FILENAME,
            media_type=_MEDIA_TYPE,
        )
        provider.responses.extend(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall(
                            "call_load",
                            "load_attachment",
                            {"handle": record.id},
                        ),
                    ),
                ),
                "已重新查看图纸。",
            ]
        )

        first_result = await agent.run(
            UserTurnInput(
                "分析这张图纸",
                artifact_handles=(record.id,),
            )
        )

        assert first_result.content == "首次分析完成。"
        first_user = agent.query_loop.message_runtime.store.all()[0]
        assert first_user.content == "分析这张图纸"
        assert len(first_user.artifact_refs) == 1
        assert first_user.artifact_refs[0].artifact_id == record.id
        assert first_user.artifact_refs[0].filename == _FILENAME
        assert first_user.artifact_refs[0].media_type == _MEDIA_TYPE
        first_request = provider.requests[0]
        _assert_catalog_state(first_request, handle=record.id, state="mounted")
        assert [item.kind for item in first_request.messages][-2:] == [
            "business_message",
            "context_mount",
        ]
        _assert_image_mount(
            first_request.messages[-1],
            handle=record.id,
            expected_heading="【用户上传附件】",
        )
        await _assert_mount_released(agent, record.id)

        second_result = await agent.run("请重新查看之前的图纸")

        assert second_result.content == "已重新查看图纸。"
        before_load = provider.requests[1]
        _assert_catalog_state(before_load, handle=record.id, state="available")
        assert all(item.kind != "context_mount" for item in before_load.messages)

        after_load = provider.requests[2]
        _assert_catalog_state(after_load, handle=record.id, state="mounted")
        tool_result_index = next(
            index
            for index, item in enumerate(after_load.messages)
            if item.kind == "tool_result" and item.tool_call_id == "call_load"
        )
        mount_index = next(
            index
            for index, item in enumerate(after_load.messages)
            if item.kind == "context_mount"
        )
        assert tool_result_index < mount_index == len(after_load.messages) - 1
        tool_result = after_load.messages[tool_result_index]
        assert isinstance(tool_result.content[0], TextPart)
        assert tool_result.content[0].text == (
            f"附件已挂载：{record.id}。"
            "附件内容将在下一次模型请求中作为当前轮次的工具结果数据提供。"
        )
        _assert_image_mount(
            after_load.messages[mount_index],
            handle=record.id,
            expected_heading="【工具结果附件】",
        )
        await _assert_mount_released(agent, record.id)

    asyncio.run(scenario())


def test_artifact_failure_cleans_mount_but_preserves_artifact() -> None:
    error = RuntimeError("provider failed")
    provider = _FailingProvider(error)
    agent = AgentBuilder().provider(provider).build(session_id="session_artifact_fail")

    async def scenario() -> None:
        record = await _upload(agent)
        with pytest.raises(RuntimeError) as caught:
            await agent.run(
                UserTurnInput("分析失败路径", artifact_handles=(record.id,))
            )
        assert caught.value is error
        assert len(provider.requests) == 1
        _assert_image_mount(
            provider.requests[0].messages[-1],
            handle=record.id,
            expected_heading="【用户上传附件】",
        )
        await _assert_mount_released(agent, record.id)

    asyncio.run(scenario())


def test_artifact_cancellation_cleans_mount_but_preserves_artifact() -> None:
    async def scenario() -> None:
        provider = _BlockingProvider()
        agent = AgentBuilder().provider(provider).build(
            session_id="session_artifact_cancel"
        )
        record = await _upload(agent)
        stream = await agent.run(
            UserTurnInput("分析取消路径", artifact_handles=(record.id,)),
            stream=True,
        )

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        await provider.entered.wait()
        assert agent.artifacts.active_mounts()
        _assert_image_mount(
            provider.requests[0].messages[-1],
            handle=record.id,
            expected_heading="【用户上传附件】",
        )
        consumer.cancel("cancel artifact turn")

        with pytest.raises(asyncio.CancelledError) as caught:
            await consumer

        assert caught.value.args == ("cancel artifact turn",)
        assert provider.cancelled.is_set()
        assert stream.closed
        await _assert_mount_released(agent, record.id)

    asyncio.run(scenario())


def test_artifact_waiting_cleans_mount_but_preserves_artifact() -> None:
    async def scenario() -> None:
        reason = WaitReason("human_input", "approval_artifact")
        wait_tool = RegisteredTool(
            name="request_artifact_approval",
            description="等待人工确认附件分析。",
            parameters={"type": "object", "properties": {}},
            handler=lambda _invocation: WaitRequest(reason),
            side_effect_policy=SideEffectPolicy.PURE,
            wait_capable=True,
        )
        provider = FakeProvider(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall(
                            "call_wait",
                            "request_artifact_approval",
                            {},
                        ),
                    ),
                )
            ]
        )
        agent = (
            AgentBuilder()
            .provider(provider)
            .tools([wait_tool])
            .build(session_id="session_artifact_waiting")
        )
        record = await _upload(agent)

        outcome = await agent.run(
            UserTurnInput("分析并等待审批", artifact_handles=(record.id,))
        )

        assert isinstance(outcome, AgentWaiting)
        assert outcome.reason == reason
        _assert_image_mount(
            provider.requests[0].messages[-1],
            handle=record.id,
            expected_heading="【用户上传附件】",
        )
        await _assert_mount_released(agent, record.id)

    asyncio.run(scenario())


class _FailingProvider:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        raise self.error


class _BlockingProvider:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        self.entered.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()


async def _upload(agent: Agent) -> ArtifactRecord:
    return await agent.artifacts.upload(
        data=_ARTIFACT_DATA,
        filename=_FILENAME,
        media_type=_MEDIA_TYPE,
    )


def _assert_catalog_state(
    request: ProviderRequest,
    *,
    handle: str,
    state: str,
) -> None:
    snapshot = request.messages[0]
    assert snapshot.kind == "context_snapshot"
    assert isinstance(snapshot.content[0], TextPart)
    assert f'handle="{handle}"' in snapshot.content[0].text
    assert f'state="{state}"' in snapshot.content[0].text


def _assert_image_mount(
    item: ProviderInputItem,
    *,
    handle: str,
    expected_heading: str,
) -> None:
    assert item.kind == "context_mount"
    assert item.persistence == "ephemeral"
    assert item.visibility == "internal"
    text, image = item.content
    assert isinstance(text, TextPart)
    assert text.text.startswith(expected_heading)
    assert handle in text.text
    assert isinstance(image, ImagePart)
    assert image.payload.handle == handle
    assert image.payload.filename == _FILENAME
    assert image.payload.media_type == _MEDIA_TYPE
    assert image.payload.data == _ARTIFACT_DATA


async def _assert_mount_released(agent: Agent, handle: str) -> None:
    assert agent.artifacts.active_mounts() == ()
    assert await agent.artifacts.read(handle) == _ARTIFACT_DATA
