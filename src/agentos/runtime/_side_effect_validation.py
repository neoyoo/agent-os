from __future__ import annotations

import unicodedata


def require_identifier(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 255
        or value != value.strip()
        or any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} must be a valid identifier")


def require_positive(value: object, field_name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def require_digest(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")


def require_stable_id(value: object, prefix: str, field_name: str) -> None:
    expected_prefix = f"{prefix}_"
    if (
        type(value) is not str
        or len(value) != len(expected_prefix) + 32
        or not value.startswith(expected_prefix)
        or any(
            character not in "0123456789abcdef"
            for character in value[len(expected_prefix):]
        )
    ):
        raise ValueError(f"{field_name} must be a canonical {prefix} id")


def validate_fence(claim_id: str | None, fencing_token: int | None) -> None:
    local = claim_id is None and fencing_token is None
    distributed = (
        type(claim_id) is str
        and bool(claim_id.strip())
        and type(fencing_token) is int
        and fencing_token > 0
    )
    if not local and not distributed:
        raise ValueError("claim_id and fencing_token must form a valid fence")


__all__ = [
    "require_digest",
    "require_identifier",
    "require_positive",
    "require_stable_id",
    "validate_fence",
]
