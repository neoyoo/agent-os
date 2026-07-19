from __future__ import annotations

import base64
import hashlib
import hmac
import json

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.runtime.errors import PayloadProtectionError
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
    payload_wire_json,
)


class FernetPayloadProtector:
    """使用 Fernet 加密并把密文绑定到 tenant/session scope。"""

    def __init__(self, key: bytes | str) -> None:
        from cryptography.fernet import Fernet

        encoded = key.encode("ascii") if type(key) is str else key
        self._fernet = Fernet(encoded)
        raw_key = base64.urlsafe_b64decode(encoded)
        self._integrity_key = hmac.new(
            raw_key,
            b"agentos.payload.integrity.v1",
            hashlib.sha256,
        ).digest()

    def __repr__(self) -> str:
        return "FernetPayloadProtector(<redacted>)"

    @staticmethod
    def generate_key() -> bytes:
        """生成新的 Fernet key；部署方负责安全保存。"""

        from cryptography.fernet import Fernet

        return Fernet.generate_key()

    def protect(
        self,
        payload: FrozenJsonObject,
        *,
        context: PayloadProtectionContext,
    ) -> ProtectedPayloadRef:
        envelope = _envelope_bytes(payload, context)
        return ProtectedPayloadRef(
            token=self._fernet.encrypt(envelope).decode("ascii"),
            digest=self._integrity_tag(envelope),
        )

    def unprotect(
        self,
        reference: ProtectedPayloadRef,
        *,
        context: PayloadProtectionContext,
    ) -> FrozenJsonObject:
        from cryptography.fernet import InvalidToken

        try:
            raw = self._fernet.decrypt(reference.token.encode("ascii"))
            envelope = json.loads(raw)
            if (
                type(envelope) is not dict
                or set(envelope) != {"payload", "scope", "version"}
                or envelope["version"] != 1
                or envelope["scope"] != _scope(context)
                or type(envelope["payload"]) is not dict
            ):
                raise ValueError("invalid protected envelope")
            payload = freeze_json_mapping(envelope["payload"])
            expected = self._integrity_tag(_envelope_bytes(payload, context))
            if not hmac.compare_digest(reference.digest, expected):
                raise ValueError("invalid protected integrity tag")
            return payload
        except (InvalidToken, KeyError, TypeError, UnicodeError, ValueError):
            raise PayloadProtectionError(
                "protected payload could not be opened",
            ) from None

    def _integrity_tag(self, envelope: bytes) -> str:
        digest = hmac.new(
            self._integrity_key,
            envelope,
            hashlib.sha256,
        ).hexdigest()
        return f"hmac-sha256:{digest}"


def _envelope_bytes(
    payload: FrozenJsonObject,
    context: PayloadProtectionContext,
) -> bytes:
    return json.dumps(
        {
            "payload": json.loads(payload_wire_json(payload)),
            "scope": _scope(context),
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _scope(context: PayloadProtectionContext) -> dict[str, str | None]:
    return {
        "invocation_id": context.invocation_id,
        "message_id": context.message_id,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "tenant_id": context.tenant_id,
        "tool_call_id": context.tool_call_id,
        "tool_name": context.tool_name,
        "turn_id": context.turn_id,
    }


__all__ = ["FernetPayloadProtector"]
