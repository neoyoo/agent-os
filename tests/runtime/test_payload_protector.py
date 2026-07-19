import hashlib

import pytest

from agentos._json_values import freeze_json_mapping
from agentos.runtime.errors import PayloadProtectionError
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
    payload_wire_json,
    protect_payload,
    unprotect_payload,
)
from agentos.security import FernetPayloadProtector


def test_fernet_payload_protector_round_trips_without_plaintext_exposure() -> None:
    marker = "secret-api-key-must-not-leak"
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    context = PayloadProtectionContext(
        tenant_id="tenant_1",
        session_id="session_1",
    )
    payload = freeze_json_mapping({"api_key": marker, "query": "drawing"})

    reference = protect_payload(protector, payload, context=context)

    assert marker not in reference.token
    assert marker not in repr(reference)
    assert marker not in repr(protector)
    raw_digest = hashlib.sha256(payload_wire_json(payload).encode("utf-8")).hexdigest()
    assert reference.digest != f"sha256:{raw_digest}"
    assert unprotect_payload(protector, reference, context=context) == payload


def test_payload_reference_is_bound_to_authorized_scope() -> None:
    protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
    payload = freeze_json_mapping({"credential": "sensitive"})
    reference = protect_payload(
        protector,
        payload,
        context=PayloadProtectionContext("tenant_1", "session_1"),
    )

    with pytest.raises(
        PayloadProtectionError,
        match="^protected payload could not be opened$",
    ) as error:
        unprotect_payload(
            protector,
            reference,
            context=PayloadProtectionContext("tenant_2", "session_1"),
        )

    assert "sensitive" not in str(error.value)
    assert reference.token not in str(error.value)


class _LeakyProtector:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    def protect(self, payload, *, context):  # type: ignore[no-untyped-def]
        raise RuntimeError(self.marker)

    def unprotect(self, reference, *, context):  # type: ignore[no-untyped-def]
        raise RuntimeError(self.marker)


def test_third_party_payload_protector_errors_are_redacted() -> None:
    marker = "third-party-secret-must-not-leak"
    protector = _LeakyProtector(marker)
    context = PayloadProtectionContext(None, "session_1")
    payload = freeze_json_mapping({"secret": marker})

    with pytest.raises(
        PayloadProtectionError,
        match="^protected payload could not be sealed$",
    ) as protect_error:
        protect_payload(protector, payload, context=context)  # type: ignore[arg-type]
    with pytest.raises(
        PayloadProtectionError,
        match="^protected payload could not be opened$",
    ) as unprotect_error:
        unprotect_payload(
            protector,  # type: ignore[arg-type]
            ProtectedPayloadRef("opaque-token", "opaque-integrity-tag"),
            context=context,
        )

    assert marker not in str(protect_error.value)
    assert marker not in str(unprotect_error.value)
