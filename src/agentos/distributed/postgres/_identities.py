from __future__ import annotations

from hashlib import sha256
import json


def stable_id(prefix: str, *parts: str) -> str:
    payload = json.dumps(
        {"parts": list(parts), "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}_{sha256(payload).hexdigest()[:32]}"


def outbox_id(tenant_id: str, source_kind: str, source_id: str) -> str:
    return stable_id("outbox", tenant_id, source_kind, source_id)


__all__ = ["outbox_id", "stable_id"]
