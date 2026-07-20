from __future__ import annotations

from collections.abc import Mapping

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.artifacts import ArtifactRef
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)


_FIELDS = {
    "version",
    "operation_id",
    "kind",
    "result_ref",
    "result_digest",
    "attestation_ref",
    "attestation_digest",
}


def side_effect_resolution_to_payload(
    resolution: SideEffectResolution,
) -> FrozenJsonObject:
    """Encode one authorized resolution into its canonical command payload."""

    if type(resolution) is not SideEffectResolution:
        raise TypeError("resolution must be SideEffectResolution")
    return freeze_json_mapping(
        {
            "version": 1,
            "operation_id": resolution.operation_id,
            "kind": resolution.kind.value,
            "result_ref": _result_ref_to_payload(resolution.result_ref),
            "result_digest": resolution.result_digest,
            "attestation_ref": _protected_ref_to_payload(
                resolution.attestation_ref,
            ),
            "attestation_digest": resolution.attestation_digest,
        },
    )


def side_effect_resolution_from_payload(
    payload: Mapping[str, object],
) -> SideEffectResolution:
    """Strictly parse a versioned resolution command payload."""

    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise ValueError("side effect resolution payload is invalid")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("side effect resolution payload version is invalid")
    try:
        return SideEffectResolution(
            operation_id=payload["operation_id"],  # type: ignore[arg-type]
            kind=SideEffectResolutionKind(payload["kind"]),
            result_ref=_result_ref_from_payload(payload["result_ref"]),
            result_digest=payload["result_digest"],  # type: ignore[arg-type]
            attestation_ref=_protected_ref_from_payload(
                payload["attestation_ref"],
            ),
            attestation_digest=payload["attestation_digest"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("side effect resolution payload is invalid") from None


def _result_ref_to_payload(reference: ToolResultRef | None) -> object:
    if reference is None:
        return None
    if type(reference) is InlineToolResultRef:
        return {"kind": "inline", "content": reference.content}
    if type(reference) is ArtifactToolResultRef:
        return {
            "kind": "artifact",
            "artifact_id": reference.artifact.artifact_id,
            "filename": reference.artifact.filename,
            "media_type": reference.artifact.media_type,
            "preview": reference.preview,
        }
    raise TypeError("resolution result reference is invalid")


def _result_ref_from_payload(value: object) -> ToolResultRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("resolution result reference is invalid")
    if set(value) == {"kind", "content"} and value["kind"] == "inline":
        return InlineToolResultRef(value["content"])  # type: ignore[arg-type]
    artifact_fields = {
        "kind",
        "artifact_id",
        "filename",
        "media_type",
        "preview",
    }
    if set(value) == artifact_fields and value["kind"] == "artifact":
        return ArtifactToolResultRef(
            ArtifactRef(
                artifact_id=value["artifact_id"],  # type: ignore[arg-type]
                filename=value["filename"],  # type: ignore[arg-type]
                media_type=value["media_type"],  # type: ignore[arg-type]
            ),
            value["preview"],  # type: ignore[arg-type]
        )
    raise ValueError("resolution result reference is invalid")


def _protected_ref_to_payload(reference: ProtectedPayloadRef | None) -> object:
    if reference is None:
        return None
    if type(reference) is not ProtectedPayloadRef:
        raise TypeError("resolution attestation reference is invalid")
    return {"token": reference.token, "digest": reference.digest}


def _protected_ref_from_payload(value: object) -> ProtectedPayloadRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"token", "digest"}:
        raise ValueError("resolution attestation reference is invalid")
    return ProtectedPayloadRef(
        value["token"],  # type: ignore[arg-type]
        value["digest"],  # type: ignore[arg-type]
    )


__all__ = [
    "side_effect_resolution_from_payload",
    "side_effect_resolution_to_payload",
]
