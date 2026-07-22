from __future__ import annotations

import argparse
import math
from collections.abc import Sequence


_COMMAND_KINDS = (
    "resume",
    "wakeup",
    "retry",
    "hitl_answer",
    "resolve_side_effect",
    "cancel",
)
_PAYLOAD_REQUIRED_KINDS = frozenset({"hitl_answer", "resolve_side_effect"})


class _CanonicalArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if (
            getattr(parsed, "command", None) == "run"
            and getattr(parsed, "action", None) == "command"
        ):
            self._normalize_command_payload(parsed)
        return parsed

    def error(self, message: str) -> None:
        del message
        raise ValueError("invalid CLI input")

    def _normalize_command_payload(self, parsed: argparse.Namespace) -> None:
        has_payload = (
            parsed.payload_json is not None or parsed.payload_file is not None
        )
        if parsed.kind in _PAYLOAD_REQUIRED_KINDS and not has_payload:
            self.error(
                f"--kind {parsed.kind} requires --payload-json or --payload-file",
            )
        if not has_payload:
            parsed.payload_json = "{}"


def build_parser() -> argparse.ArgumentParser:
    """构建不加载 factory 或运行时依赖的 canonical CLI parser。"""

    parser = _CanonicalArgumentParser(prog="agent-os")
    parser.add_argument("--factory")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init")
    init_parser.add_argument("path")

    _add_run_commands(commands.add_parser("run"))
    _add_artifact_commands(commands.add_parser("artifact"))
    _add_worker_commands(commands.add_parser("worker"))
    _add_relay_commands(commands.add_parser("relay"))

    commands.add_parser("serve")
    migrate_parser = commands.add_parser("migrate")
    migrate_parser.add_argument("--check", action="store_true")
    return parser


def _add_run_commands(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)

    submit_parser = actions.add_parser("submit")
    _add_scope_arguments(submit_parser)
    submit_parser.add_argument("--submission-id", required=True)
    content = submit_parser.add_mutually_exclusive_group(required=True)
    content.add_argument("--content")
    content.add_argument("--content-file")
    content.add_argument("--stdin", action="store_true")
    submit_parser.add_argument("--artifact", action="append", default=[])

    command_parser = actions.add_parser("command")
    _add_scope_arguments(command_parser)
    command_parser.add_argument("--run-id", required=True)
    command_parser.add_argument("--command-id", required=True)
    command_parser.add_argument("--kind", choices=_COMMAND_KINDS, required=True)
    payload = command_parser.add_mutually_exclusive_group()
    payload.add_argument("--payload-json")
    payload.add_argument("--payload-file")

    get_parser = actions.add_parser("get")
    _add_scope_arguments(get_parser)
    get_parser.add_argument("--run-id", required=True)

    watch_parser = actions.add_parser("watch")
    _add_scope_arguments(watch_parser)
    watch_parser.add_argument("--run-id", required=True)
    watch_parser.add_argument("--cursor")


def _add_artifact_commands(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)

    upload_parser = actions.add_parser("upload")
    _add_scope_arguments(upload_parser)
    upload_parser.add_argument("--upload-id", required=True)
    upload_parser.add_argument("--file", required=True)
    upload_parser.add_argument("--media-type", required=True)

    list_parser = actions.add_parser("list")
    _add_scope_arguments(list_parser)
    list_parser.add_argument("--cursor")
    list_parser.add_argument("--limit", type=_artifact_limit)

    read_parser = actions.add_parser("read")
    _add_scope_arguments(read_parser)
    read_parser.add_argument("--artifact-id", required=True)
    read_parser.add_argument("--output", required=True)

    delete_parser = actions.add_parser("delete")
    _add_scope_arguments(delete_parser)
    delete_parser.add_argument("--artifact-id", required=True)
    delete_parser.add_argument("--deletion-id", required=True)


def _add_worker_commands(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    start_parser = actions.add_parser("start")
    start_parser.add_argument("--drain-timeout", type=_non_negative_seconds)


def _add_relay_commands(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    start_parser = actions.add_parser("start")
    start_parser.add_argument("--idle-interval", type=_positive_seconds)


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--session-id", required=True)


def _artifact_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return limit


def _non_negative_seconds(value: str) -> float:
    seconds = _seconds(value)
    if seconds < 0:
        raise argparse.ArgumentTypeError("seconds must not be negative")
    return seconds


def _positive_seconds(value: str) -> float:
    seconds = _seconds(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("seconds must be positive")
    return seconds


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("seconds must be a number") from error
    if not math.isfinite(seconds):
        raise argparse.ArgumentTypeError("seconds must be finite")
    return seconds


__all__ = ["build_parser"]
