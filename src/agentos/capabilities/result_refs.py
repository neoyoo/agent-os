from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agentos.artifacts.types import ArtifactRef


@dataclass(frozen=True, slots=True)
class InlineToolResultRef:
    """保存预算内、可直接恢复的 Provider Tool Result。"""

    content: str

    def __post_init__(self) -> None:
        if type(self.content) is not str:
            raise TypeError("inline tool result content must be str")


@dataclass(frozen=True, slots=True)
class ArtifactToolResultRef:
    """引用 ArtifactStore 中的大型 Tool Result，并保留有界 preview。"""

    artifact: ArtifactRef
    preview: str

    def __post_init__(self) -> None:
        if type(self.artifact) is not ArtifactRef:
            raise TypeError("artifact tool result requires ArtifactRef")
        if type(self.preview) is not str:
            raise TypeError("artifact tool result preview must be str")


ToolResultRef: TypeAlias = InlineToolResultRef | ArtifactToolResultRef


__all__ = ["ArtifactToolResultRef", "InlineToolResultRef", "ToolResultRef"]
