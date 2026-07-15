from dataclasses import dataclass

from agentos.artifacts.types import ArtifactMountReason
from agentos.events.types import AgentEvent


@dataclass(frozen=True, slots=True)
class ArtifactUploadedEvent(AgentEvent):
    """Artifact 内容和元数据已写入 Store。"""

    handle: str = ""
    filename: str | None = None
    media_type: str = ""
    size_bytes: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactLoadRequestedEvent(AgentEvent):
    """Runtime 已收到语法有效的 Artifact 加载请求。"""

    handle: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactMountedEvent(AgentEvent):
    """Artifact 已挂载到当前 Turn。"""

    handle: str = ""
    filename: str | None = None
    media_type: str = ""
    reason: ArtifactMountReason = "tool_result"


@dataclass(frozen=True, slots=True)
class ArtifactUnmountedEvent(AgentEvent):
    """Artifact 已从当前 Turn 卸载。"""

    handle: str = ""
    reason: ArtifactMountReason = "tool_result"


@dataclass(frozen=True, slots=True)
class ArtifactDeletedEvent(AgentEvent):
    """Artifact 内容和元数据已从 Store 删除。"""

    handle: str = ""
    filename: str | None = None
    media_type: str = ""
    size_bytes: int = 0
