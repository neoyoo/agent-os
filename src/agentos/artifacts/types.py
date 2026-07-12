from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """StoredMessage 保存的附件轻量引用。"""

    artifact_id: str
    filename: str | None
    media_type: str
