from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import parse_qsl, urlsplit

from agentos._redaction import is_secret_like_key, redact_secret_patterns
from agentos.planning.models import PlanState
from agentos.planning.sqlite_errors import SQLitePlanStoreUnsafeError


_SIGNED_QUERY_KEYS = frozenset(
    {"sig", "signature", "x-amz-signature", "x-goog-signature"}
)


def validate_durable_plan(plan: PlanState) -> None:
    """拒绝 SQLite Durable Profile 明确禁止的路径和凭据数据。"""

    if plan.workspace is not None:
        if plan.workspace.root is not None and _is_absolute(plan.workspace.root):
            _unsafe()
        _validate_metadata(plan.workspace.metadata)
    for evidence in plan.evidence:
        if evidence.uri is not None:
            _validate_uri(evidence.uri)
        _validate_metadata(evidence.metadata)


def _validate_uri(uri: str) -> None:
    if _is_absolute(uri):
        _unsafe()
    parsed = urlsplit(uri)
    if parsed.scheme.lower() == "file" or parsed.username or parsed.password:
        _unsafe()
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.lower()
        if is_secret_like_key(normalized) or normalized in _SIGNED_QUERY_KEYS:
            _unsafe()
    if redact_secret_patterns(uri) != uri:
        _unsafe()


def _validate_metadata(metadata: Mapping[str, str]) -> None:
    for key, value in metadata.items():
        if is_secret_like_key(str(key)):
            _unsafe()
        text = str(value)
        if _is_absolute(text) or redact_secret_patterns(text) != text:
            _unsafe()


def _is_absolute(value: str) -> bool:
    return PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()


def _unsafe() -> None:
    raise SQLitePlanStoreUnsafeError("plan contains unsafe durable data")


__all__ = ["validate_durable_plan"]
