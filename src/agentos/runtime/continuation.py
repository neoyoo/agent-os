"""Continuation Turn 的类型化数据投影。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Literal, TypeAlias

from agentos.providers.input import ProviderInputItem
from agentos._json_values import thaw_json_value
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import AcceptedInternalStartInput
from agentos.runtime.internal_start import canonical_internal_start_payload


ContinuationNoticeKind: TypeAlias = Literal["task_completed", "team_message"]

_NOTICE_KINDS = frozenset({"task_completed", "team_message"})


@dataclass(frozen=True, slots=True)
class ContinuationNotice:
    """触发 Continuation Turn 的无指令权限 Runtime 事实。"""

    kind: ContinuationNoticeKind
    subject_id: str
    action: str

    def __post_init__(self) -> None:
        if self.kind not in _NOTICE_KINDS:
            raise ValueError("unsupported continuation notice kind")
        _require_non_empty_string(self.subject_id, "subject_id")
        _require_non_empty_string(self.action, "action")


class ContinuationRuntime:
    """Own current Turn continuation facts and their Message Plane projection."""

    def __init__(self) -> None:
        self._notices: tuple[ContinuationNotice, ...] = ()
        self._durable: AcceptedContinuationInput | None = None
        self._internal_start: AcceptedInternalStartInput | None = None

    def set_notices(self, notices: tuple[ContinuationNotice, ...]) -> None:
        if not notices or any(type(item) is not ContinuationNotice for item in notices):
            raise TypeError("continuation runtime requires typed notices")
        self._notices = tuple(notices)
        self._durable = None
        self._internal_start = None

    def set_durable(self, continuation: AcceptedContinuationInput) -> None:
        if type(continuation) is not AcceptedContinuationInput:
            raise TypeError("durable continuation input is invalid")
        self._notices = ()
        self._durable = continuation
        self._internal_start = None

    def set_internal_start(self, input: AcceptedInternalStartInput) -> None:
        """替换当前 Turn 的 internal-start 临时投影。"""

        if type(input) is not AcceptedInternalStartInput:
            raise TypeError("internal start input is invalid")
        self._notices = ()
        self._durable = None
        self._internal_start = input

    def clear(self) -> None:
        self._notices = ()
        self._durable = None
        self._internal_start = None

    def inputs(self) -> tuple[ProviderInputItem, ...]:
        if self._durable is not None:
            return (project_durable_continuation(self._durable),)
        if self._internal_start is not None:
            return (project_internal_start(self._internal_start),)
        return () if not self._notices else (project_continuation_data(self._notices),)


def project_continuation_data(
    notices: Iterable[ContinuationNotice],
) -> ProviderInputItem:
    """按输入顺序生成一次性 ContinuationData Provider 投影。"""

    if isinstance(notices, (str, bytes)):
        raise TypeError("continuation data requires ContinuationNotice values")
    normalized = tuple(notices)
    if not normalized:
        raise ValueError("continuation data requires at least one notice")
    if any(type(notice) is not ContinuationNotice for notice in normalized):
        raise TypeError("continuation data requires ContinuationNotice values")

    lines = [
        '<continuation-data protocol="agentos.continuation" version="1.0"\n',
        '    origin="runtime" authority="context-data" persistence="ephemeral"\n',
        '    visibility="internal">\n',
    ]
    for notice in normalized:
        lines.extend(
            (
                f'  <notice kind="{notice.kind}" '
                f'subject-id="{_escape_attribute(notice.subject_id)}"\n',
                f'      action="{_escape_attribute(notice.action)}"/>\n',
            )
        )
    lines.append("</continuation-data>\n")
    return ProviderInputItem.continuation_data("".join(lines))


def project_durable_continuation(
    continuation: AcceptedContinuationInput,
) -> ProviderInputItem:
    """投影 Store 已批准的单 Turn Durable continuation data。"""

    if type(continuation) is not AcceptedContinuationInput:
        raise TypeError("durable continuation input is invalid")
    if continuation.kind == "resolve_side_effect":
        raise ValueError("side effect resolution is runtime control")
    payload = json.dumps(
        thaw_json_value(continuation.payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    xml = (
        '<continuation-data protocol="agentos.continuation" version="1.0"\n'
        '    origin="runtime" authority="context-data" persistence="ephemeral"\n'
        '    visibility="internal" source="durable-command"\n'
        f'    run-id="{_escape_attribute(continuation.run_id)}"\n'
        f'    command-id="{_escape_attribute(continuation.command_id)}"\n'
        f'    turn-id="{_escape_attribute(continuation.turn_id)}"\n'
        f'    kind="{continuation.kind}">\n'
        f"  <payload-json>{_escape_attribute(payload)}</payload-json>\n"
        "</continuation-data>\n"
    )
    return ProviderInputItem.continuation_data(xml)


def project_internal_start(input: AcceptedInternalStartInput) -> ProviderInputItem:
    """投影不具指令权限的 Team internal-start 数据。"""

    if type(input) is not AcceptedInternalStartInput:
        raise TypeError("internal start input is invalid")
    payload = canonical_internal_start_payload(input.source_payload)
    xml = (
        '<continuation-data protocol="agentos.continuation" version="1.0"\n'
        '    origin="runtime" authority="context-data" persistence="ephemeral"\n'
        '    visibility="internal" source="internal-start" kind="team_message">\n'
        f"  <payload-json>{_escape_attribute(payload)}</payload-json>\n"
        "</continuation-data>\n"
    )
    return ProviderInputItem.continuation_data(xml)


def _require_non_empty_string(value: object, field: str) -> None:
    if type(value) is not str:
        raise TypeError(f"continuation notice {field} must be str")
    if not value:
        raise ValueError(f"continuation notice {field} must not be empty")


def _escape_attribute(value: str) -> str:
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&apos;",
        "\r": "&#xD;",
        "\n": "&#xA;",
        "\t": "&#x9;",
    }
    escaped: list[str] = []
    for character in value:
        _validate_xml_character(character)
        escaped.append(replacements.get(character, character))
    return "".join(escaped)


def _validate_xml_character(character: str) -> None:
    code = ord(character)
    if not (
        code in (0x9, 0xA, 0xD)
        or 0x20 <= code <= 0xD7FF
        or 0xE000 <= code <= 0xFFFD
        or 0x10000 <= code <= 0x10FFFF
    ):
        raise ValueError("invalid XML character")
