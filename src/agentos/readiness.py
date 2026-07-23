from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentos._readiness_forms import (
    AgentFormReadiness as AgentFormReadiness,
    REQUIRED_READINESS_DIMENSIONS as REQUIRED_READINESS_DIMENSIONS,
    ReadinessDimension as ReadinessDimension,
    ReadinessLevel as ReadinessLevel,
    get_agent_form_readiness as get_agent_form_readiness,
    list_agent_form_readiness as list_agent_form_readiness,
)


ReadinessEvidenceStatus = Literal["passed", "failed", "skipped", "unknown"]

_READINESS_EVIDENCE_STATUSES: tuple[str, ...] = (
    "passed",
    "failed",
    "skipped",
    "unknown",
)

_SECRET_KEY_PARTS = frozenset(
    {
        "api",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "token",
    },
)


@dataclass(frozen=True, slots=True)
class ReadinessEvidenceCheck:
    """One normalized release-readiness evidence check."""

    check_name: str
    status: ReadinessEvidenceStatus
    evidence: Mapping[str, object]
    required: bool = True
    source: str = "readiness"
    blocking_reason: str = ""
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.check_name.strip():
            raise ValueError("check_name must not be empty")
        if self.status not in _READINESS_EVIDENCE_STATUSES:
            raise ValueError("status must be passed, failed, skipped, or unknown")
        if not self.source.strip():
            raise ValueError("source must not be empty")

    @classmethod
    def from_payload(
        cls,
        check_name: str,
        payload: object,
        *,
        required: bool | None = None,
        source: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ReadinessEvidenceCheck:
        """Normalize an existing readiness/profile/gate payload."""

        payload_mapping = payload if isinstance(payload, Mapping) else {}
        if required is None:
            required = _required_from_payload(payload_mapping, default=True)
        status = _status_from_payload(payload)
        return cls(
            check_name=check_name,
            status=status,
            evidence=_evidence_mapping(payload),
            required=required,
            source=source or _source_from_payload(payload_mapping),
            blocking_reason=_blocking_reason_from_payload(
                payload_mapping,
                status=status,
                required=required,
            ),
            metadata=_json_safe_mapping(metadata or {}),
        )

    @property
    def blocks_production_readiness(self) -> bool:
        """Return whether this check should block a production release."""

        return self.required and self.status != "passed"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe check payload."""

        return {
            "check_name": self.check_name,
            "status": self.status,
            "required": self.required,
            "source": self.source,
            "blocking_reason": self.blocking_reason,
            "block_production_readiness": self.blocks_production_readiness,
            "evidence": _json_safe_mapping(self.evidence),
            "metadata": _json_safe_mapping(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class ProductionReadinessEvidenceBundle:
    """Release gate over already-produced readiness evidence payloads."""

    checks: tuple[ReadinessEvidenceCheck, ...]
    required_checks: tuple[str, ...]
    bundle_name: str = "production_readiness"
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.bundle_name.strip():
            raise ValueError("bundle_name must not be empty")
        _validate_names(self.required_checks, field_name="required_checks")
        _validate_names(
            tuple(check.check_name for check in self.checks),
            field_name="checks",
        )
        seen: set[str] = set()
        for check in self.checks:
            if check.check_name in seen:
                raise ValueError("checks must not contain duplicate check names")
            seen.add(check.check_name)

    @classmethod
    def from_sources(
        cls,
        sources: Mapping[str, object],
        *,
        required_checks: tuple[str, ...] = (),
        bundle_name: str = "production_readiness",
        metadata: Mapping[str, object] | None = None,
    ) -> ProductionReadinessEvidenceBundle:
        """Build a release evidence bundle from existing readiness sources."""

        if not sources:
            raise ValueError("sources must not be empty")
        _validate_names(tuple(sources), field_name="sources")
        required = required_checks or tuple(sources)
        _validate_names(required, field_name="required_checks")
        required_set = set(required)
        checks: list[ReadinessEvidenceCheck] = []
        for name, source in sources.items():
            payload = _resolve_readiness_source(source)
            payload_mapping = payload if isinstance(payload, Mapping) else {}
            checks.append(
                ReadinessEvidenceCheck.from_payload(
                    name,
                    payload,
                    required=_required_from_payload(
                        payload_mapping,
                        default=name in required_set,
                    ),
                    source=_readiness_source_name(source),
                ),
            )
        return cls(
            checks=tuple(checks),
            required_checks=tuple(required),
            bundle_name=bundle_name,
            metadata=metadata,
        )

    @property
    def checks_by_name(self) -> dict[str, ReadinessEvidenceCheck]:
        """Return checks keyed by stable check name."""

        return {check.check_name: check for check in self.checks}

    @property
    def missing_required_checks(self) -> tuple[str, ...]:
        """Return required check names with no evidence payload."""

        checks = self.checks_by_name
        return tuple(name for name in self.required_checks if name not in checks)

    @property
    def blocking_checks(self) -> tuple[str, ...]:
        """Return check names that block production readiness."""

        return tuple(
            check.check_name
            for check in self.checks
            if check.blocks_production_readiness
        )

    @property
    def accepted(self) -> bool:
        """Return whether all required release evidence is present and passing."""

        return not self.missing_required_checks and not self.blocking_checks

    @property
    def block_production_readiness(self) -> bool:
        """Return whether this bundle should block production readiness."""

        return not self.accepted

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe release gate evidence bundle."""

        return {
            "bundle_name": self.bundle_name,
            "status": "ok" if self.accepted else "failed",
            "accepted": self.accepted,
            "block_production_readiness": self.block_production_readiness,
            "required_checks": self.required_checks,
            "missing_required_checks": self.missing_required_checks,
            "blocking_checks": self.blocking_checks,
            "checks": tuple(check.as_dict() for check in self.checks),
            "metadata": _json_safe_mapping(self.metadata or {}),
            "sdk_owned": (
                "ProductionReadinessEvidenceBundle",
                "ReadinessEvidenceCheck",
                "ReadinessEvidenceStatus",
                "release gate evidence bundle",
                "JSON-safe evidence bundle",
                "blocking_checks",
                "missing_required_checks",
                "sdk_owned/deployment_owned boundary metadata",
            ),
            "deployment_owned": (
                "backend check execution",
                "provider and backend credentials",
                "network, TLS, gateway, and tenant policy",
                "migration execution",
                "CI matrix execution",
                "artifact retention",
                "release approval, rollout, rollback, alerting, and runbooks",
            ),
        }

    def readiness_metadata(self) -> dict[str, object]:
        """Return readiness-compatible bundle metadata."""

        return {
            "ready": self.accepted,
            **self.as_dict(),
        }

    def readiness_check(self) -> dict[str, object]:
        """Return an ASGI readiness-compatible check payload."""

        return {
            **self.readiness_metadata(),
            "ok": self.accepted,
        }


def _resolve_readiness_source(source: object) -> object:
    if callable(source):
        return source()
    for method_name in ("readiness_check", "readiness_metadata", "as_dict"):
        method = getattr(source, method_name, None)
        if callable(method):
            return method()
    return source


def _readiness_source_name(source: object) -> str:
    if callable(source):
        return getattr(source, "__name__", source.__class__.__name__)
    return source.__class__.__name__


def _required_from_payload(
    payload: Mapping[str, object],
    *,
    default: bool,
) -> bool:
    value = payload.get("required")
    if isinstance(value, bool):
        return value
    return default


def _status_from_payload(payload: object) -> ReadinessEvidenceStatus:
    if isinstance(payload, bool):
        return "passed" if payload else "failed"
    if not isinstance(payload, Mapping):
        return "unknown"
    if payload.get("block_production_readiness") is True:
        return "failed"
    if payload.get("block_plan_creation") is True:
        return "failed"
    for boolean_key in ("ok", "ready", "accepted"):
        value = payload.get(boolean_key)
        if isinstance(value, bool):
            return "passed" if value else "failed"
    status = payload.get("status")
    if isinstance(status, bool):
        return "passed" if status else "failed"
    if isinstance(status, str):
        normalized = status.lower()
        if normalized in {"ok", "ready", "pass", "passed", "success", "healthy"}:
            return "passed"
        if normalized in {
            "fail",
            "failed",
            "failure",
            "error",
            "blocked",
            "unhealthy",
        }:
            return "failed"
        if normalized in {"skip", "skipped"}:
            return "skipped"
        if normalized == "unknown":
            return "unknown"
    return "unknown"


def _evidence_mapping(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return _json_safe_mapping(payload)
    return {"value": _json_safe_value(payload)}


def _source_from_payload(payload: Mapping[str, object]) -> str:
    source = payload.get("source")
    if isinstance(source, str) and source.strip():
        return source
    return "readiness"


def _blocking_reason_from_payload(
    payload: Mapping[str, object],
    *,
    status: ReadinessEvidenceStatus,
    required: bool,
) -> str:
    reason = payload.get("blocking_reason")
    if isinstance(reason, str):
        return reason
    if not required or status == "passed":
        return ""
    if status == "skipped":
        return "required readiness evidence was skipped"
    if status == "unknown":
        return "required readiness evidence status is unknown"
    return "required readiness evidence failed"


def _validate_names(values: tuple[str, ...], *, field_name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain empty names")


def _json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _json_safe_value(value, key_hint=str(key))
        for key, value in values.items()
    }


def _json_safe_value(value: object, *, key_hint: str = "") -> object:
    if _is_secret_like_key(key_hint):
        return "<redacted>"
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return tuple(_json_safe_value(item) for item in value)
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    return repr(value)


def _is_secret_like_key(key: str) -> bool:
    lowered = key.lower()
    parts = {
        part
        for part in lowered.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if lowered in _SECRET_KEY_PARTS:
        return True
    return bool(parts & _SECRET_KEY_PARTS)


__all__ = [
    "AgentFormReadiness",
    "ProductionReadinessEvidenceBundle",
    "REQUIRED_READINESS_DIMENSIONS",
    "ReadinessDimension",
    "ReadinessEvidenceCheck",
    "ReadinessEvidenceStatus",
    "ReadinessLevel",
    "get_agent_form_readiness",
    "list_agent_form_readiness",
]
