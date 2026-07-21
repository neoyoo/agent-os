from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._protojson import (
    A2A_UNSET,
    decode_proto_bytes,
    encode_proto_bytes,
    proto_enum,
    proto_enum_json,
    proto_fields,
    repeated,
    required_field,
)
from agentos.transports.a2a.message_types import (
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2ARole,
)


MAX_INLINE_FILE_BYTES = 512 * 1024


def part_to_dict(part: A2APart) -> dict[str, object]:
    payload: dict[str, object] = {}
    if part.text is not A2A_UNSET:
        payload["text"] = part.text
    elif part.raw is not A2A_UNSET:
        payload["raw"] = encode_proto_bytes(part.raw)  # type: ignore[arg-type]
    elif part.url is not A2A_UNSET:
        payload["url"] = part.url
    else:
        payload["data"] = thaw_json_value(part.data)  # type: ignore[arg-type]
    if part.metadata is not None:
        payload["metadata"] = thaw_json_value(part.metadata)
    if part.filename is not None:
        payload["filename"] = part.filename
    if part.media_type is not None:
        payload["mediaType"] = part.media_type
    return payload


def part_from_dict(
    value: object,
    *,
    reject_url: bool = False,
    raw_counter: list[int] | None = None,
) -> A2APart:
    if type(value) is not dict:
        raise ValueError("part must be an object")
    if "kind" in value or "file" in value:
        raise ValueError("legacy Part fields are not supported")
    fields = proto_fields(
        value,
        {
            "text": "text",
            "raw": "raw",
            "url": "url",
            "data": "data",
            "metadata": "metadata",
            "filename": "filename",
            "mediaType": "media_type",
        },
        field_name="part",
    )
    selected: list[tuple[str, object]] = []
    for name in ("text", "raw", "url"):
        if name in fields and fields[name] is not None:
            selected.append((name, fields[name]))
    if "data" in fields:
        selected.append(("data", fields["data"]))
    if len(selected) != 1:
        raise ValueError("part must select exactly one content field")
    name, content = selected[0]
    kwargs: dict[str, object] = {
        "metadata": _optional_object(fields.get("metadata"), "part metadata"),
        "filename": _optional_string(fields.get("filename"), "part filename"),
        "media_type": _optional_string(fields.get("mediaType"), "part mediaType"),
    }
    if name == "text":
        kwargs["text"] = require_string(content, "part text", empty=True)
    elif name == "raw":
        raw = decode_proto_bytes(content, "part raw")
        if raw_counter is not None:
            raw_counter[0] += len(raw)
            if raw_counter[0] > MAX_INLINE_FILE_BYTES:
                raise ValueError("inline raw parts exceed the decoded-size limit")
        kwargs["raw"] = raw
    elif name == "url":
        if reject_url:
            raise ValueError("inbound URL parts are not supported")
        kwargs["url"] = require_string(content, "part url")
    else:
        kwargs["data"] = content
    return A2APart(**kwargs)


def message_to_dict(message: A2AMessage) -> dict[str, object]:
    payload: dict[str, object] = {"messageId": message.message_id}
    if message.context_id is not None:
        payload["contextId"] = message.context_id
    if message.task_id is not None:
        payload["taskId"] = message.task_id
    payload["role"] = proto_enum_json(message.role)
    payload["parts"] = [part_to_dict(part) for part in message.parts]
    if message.metadata is not None:
        payload["metadata"] = thaw_json_value(message.metadata)
    if message.extensions:
        payload["extensions"] = list(message.extensions)
    if message.reference_task_ids:
        payload["referenceTaskIds"] = list(message.reference_task_ids)
    return payload


def message_from_dict(
    value: object,
    *,
    inbound: bool = False,
    raw_counter: list[int] | None = None,
) -> A2AMessage:
    fields = proto_fields(
        value,
        {
            "messageId": "message_id",
            "contextId": "context_id",
            "taskId": "task_id",
            "role": "role",
            "parts": "parts",
            "metadata": "metadata",
            "extensions": "extensions",
            "referenceTaskIds": "reference_task_ids",
        },
        field_name="message",
    )
    role = proto_enum(
        required_field(fields, "role", "message role"), A2ARole, "message role"
    )
    if role not in {A2ARole.ROLE_USER, A2ARole.ROLE_AGENT}:
        raise ValueError("message role is invalid")
    return A2AMessage(
        message_id=require_string(
            required_field(fields, "messageId", "messageId"),
            "messageId",
        ),
        context_id=_optional_string(fields.get("contextId"), "contextId"),
        task_id=_optional_string(fields.get("taskId"), "taskId"),
        role=role,
        parts=tuple(
            part_from_dict(item, reject_url=inbound, raw_counter=raw_counter)
            for item in repeated(fields.get("parts"), "message parts")
        ),
        metadata=_optional_object(fields.get("metadata"), "message metadata"),
        extensions=_string_tuple(fields.get("extensions"), "message extensions"),
        reference_task_ids=_string_tuple(
            fields.get("referenceTaskIds"),
            "referenceTaskIds",
        ),
    )


def artifact_to_dict(artifact: A2AArtifact) -> dict[str, object]:
    payload: dict[str, object] = {"artifactId": artifact.artifact_id}
    if artifact.name is not None:
        payload["name"] = artifact.name
    if artifact.description is not None:
        payload["description"] = artifact.description
    payload["parts"] = [part_to_dict(part) for part in artifact.parts]
    if artifact.metadata is not None:
        payload["metadata"] = thaw_json_value(artifact.metadata)
    if artifact.extensions:
        payload["extensions"] = list(artifact.extensions)
    return payload


def artifact_from_dict(value: object) -> A2AArtifact:
    fields = proto_fields(
        value,
        {
            "artifactId": "artifact_id",
            "name": "name",
            "description": "description",
            "parts": "parts",
            "metadata": "metadata",
            "extensions": "extensions",
        },
        field_name="artifact",
    )
    return A2AArtifact(
        artifact_id=require_string(
            required_field(fields, "artifactId", "artifactId"),
            "artifactId",
        ),
        name=_optional_string(fields.get("name"), "artifact name"),
        description=_optional_string(fields.get("description"), "artifact description"),
        parts=tuple(
            part_from_dict(item)
            for item in repeated(fields.get("parts"), "artifact parts")
        ),
        metadata=_optional_object(fields.get("metadata"), "artifact metadata"),
        extensions=_string_tuple(fields.get("extensions"), "artifact extensions"),
    )


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return require_string(value, field_name)


def _optional_object(value: object, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    return tuple(
        require_string(item, field_name) for item in repeated(value, field_name)
    )


__all__ = [
    "MAX_INLINE_FILE_BYTES",
    "artifact_from_dict",
    "artifact_to_dict",
    "message_from_dict",
    "message_to_dict",
    "part_from_dict",
    "part_to_dict",
]
