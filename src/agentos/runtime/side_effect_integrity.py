from __future__ import annotations

from datetime import datetime

from agentos._waiting import WaitReason
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.runtime.tool_identity import canonical_digest


def result_ref_digest(reference: ToolResultRef) -> str:
    """返回 canonical Tool Result ref digest。"""

    if type(reference) is InlineToolResultRef:
        value: object = {
            "content": reference.content,
            "kind": "inline",
            "version": 1,
        }
    elif type(reference) is ArtifactToolResultRef:
        value = {
            "artifact": {
                "artifact_id": reference.artifact.artifact_id,
                "filename": reference.artifact.filename,
                "mime_type": reference.artifact.media_type,
            },
            "kind": "artifact",
            "preview": reference.preview,
            "version": 1,
        }
    else:
        raise TypeError("reference must be a ToolResultRef")
    return canonical_digest(value)


def wait_reason_digest(reason: WaitReason) -> str:
    """返回 WAITING atomic completion 使用的 canonical digest。"""

    if type(reason) is not WaitReason:
        raise TypeError("reason must be WaitReason")
    return canonical_digest(
        {
            "detail": reason.detail,
            "handle": reason.handle,
            "kind": reason.kind,
            "not_before": _datetime_value(reason.not_before),
            "version": 1,
        },
    )


def _datetime_value(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = ["result_ref_digest", "wait_reason_digest"]
