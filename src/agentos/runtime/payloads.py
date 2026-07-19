from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from agentos._json_values import (
    FrozenJsonObject,
    freeze_json_mapping,
    thaw_json_value,
)
from agentos.runtime.errors import PayloadProtectionError


@dataclass(frozen=True, slots=True, repr=False)
class ProtectedPayloadRef:
    """PayloadProtector 产生的 opaque envelope。"""

    token: str
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.token, "token")
        _require_text(self.digest, "digest")

    def __repr__(self) -> str:
        return "ProtectedPayloadRef(<redacted>)"


@dataclass(frozen=True, slots=True)
class PayloadProtectionContext:
    """加解密时必须完全匹配的授权数据范围。"""

    tenant_id: str | None
    session_id: str
    run_id: str | None = None
    turn_id: str | None = None
    invocation_id: str | None = None
    message_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None

    def __post_init__(self) -> None:
        if self.tenant_id is not None:
            _require_text(self.tenant_id, "tenant_id")
        _require_text(self.session_id, "session_id")
        execution_values = (
            self.run_id,
            self.turn_id,
            self.invocation_id,
            self.message_id,
            self.tool_call_id,
            self.tool_name,
        )
        if any(value is not None for value in execution_values):
            if any(value is None for value in execution_values):
                raise ValueError(
                    "payload execution context must be complete",
                )
            for field_name, value in zip(
                (
                    "run_id",
                    "turn_id",
                    "invocation_id",
                    "message_id",
                    "tool_call_id",
                    "tool_name",
                ),
                execution_values,
                strict=True,
            ):
                _require_text(value, field_name)


class PayloadProtector(Protocol):
    """保护和恢复持久 Tool arguments 的加密边界。"""

    def protect(
        self,
        payload: FrozenJsonObject,
        *,
        context: PayloadProtectionContext,
    ) -> ProtectedPayloadRef: ...

    def unprotect(
        self,
        reference: ProtectedPayloadRef,
        *,
        context: PayloadProtectionContext,
    ) -> FrozenJsonObject: ...


def protect_payload(
    protector: PayloadProtector,
    payload: FrozenJsonObject,
    *,
    context: PayloadProtectionContext,
) -> ProtectedPayloadRef:
    """保护 immutable JSON，并校验实现返回的完整性摘要。"""

    if type(payload) is not FrozenJsonObject:
        raise TypeError("protected payload must be a FrozenJsonObject")
    try:
        reference = protector.protect(payload, context=context)
        if type(reference) is not ProtectedPayloadRef:
            raise TypeError("invalid protected reference")
        return reference
    except Exception:
        raise PayloadProtectionError(
            "protected payload could not be sealed",
        ) from None


def unprotect_payload(
    protector: PayloadProtector,
    reference: ProtectedPayloadRef,
    *,
    context: PayloadProtectionContext,
) -> FrozenJsonObject:
    """在授权 scope 内恢复 JSON，并再次验证完整性摘要。"""

    try:
        payload = protector.unprotect(reference, context=context)
        return freeze_json_mapping(payload)
    except Exception:
        raise PayloadProtectionError(
            "protected payload could not be opened",
        ) from None


def payload_wire_json(payload: FrozenJsonObject) -> str:
    """返回供加密 Adapter 使用的 canonical JSON。"""

    return json.dumps(
        thaw_json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_text(value: object, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


__all__ = [
    "PayloadProtectionContext",
    "PayloadProtector",
    "ProtectedPayloadRef",
    "payload_wire_json",
    "protect_payload",
    "unprotect_payload",
]
