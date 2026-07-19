import asyncio

from agentos import AgentBuilder
from agentos.providers import FakeProvider, ImagePart, TextPart
from agentos.runtime import UserTurnInput
from agentos.durable import DurableRuntimeProfile


ARTIFACT_BYTES = b"\x89PNG\r\nphase-5-restart-image"


def _profile(tmp_path, provider: FakeProvider) -> DurableRuntimeProfile:
    return DurableRuntimeProfile(
        agent_builder=AgentBuilder().provider(provider),
        database_path=tmp_path / "state.db",
        artifact_root=tmp_path / "artifacts",
    )


def test_artifact_bytes_reload_and_project_after_profile_restart(tmp_path) -> None:
    with _profile(tmp_path, FakeProvider([])) as first:
        record = asyncio.run(first.build_agent("session_1")).artifacts.upload(
            data=ARTIFACT_BYTES,
            filename="drawing.png",
            media_type="image/png",
        )

    provider = FakeProvider(["image restored"])
    with _profile(tmp_path, provider) as restarted:
        agent = asyncio.run(restarted.build_agent("session_1"))
        result = asyncio.run(
            agent.run(
                UserTurnInput(
                    "重新查看图纸",
                    artifact_handles=(record.id,),
                )
            )
        )
        assert agent.artifacts.read(record.id) == ARTIFACT_BYTES

    assert result.content == "image restored"
    snapshot = provider.requests[0].messages[0]
    assert isinstance(snapshot.content[0], TextPart)
    assert f'handle="{record.id}"' in snapshot.content[0].text
    mount = provider.requests[0].messages[-1]
    assert mount.kind == "context_mount"
    text, image = mount.content
    assert isinstance(text, TextPart)
    assert text.text.startswith("【用户上传附件】")
    assert isinstance(image, ImagePart)
    assert image.payload.handle == record.id
    assert image.payload.data == ARTIFACT_BYTES
    assert ARTIFACT_BYTES not in (tmp_path / "state.db").read_bytes()
