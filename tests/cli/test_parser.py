from __future__ import annotations

import argparse

import pytest

from agentos.cli.parser import build_parser


_SCOPE = ("--tenant", "tenant_1", "--session-id", "session_1")
_COMMAND = (
    "run",
    "command",
    *_SCOPE,
    "--run-id",
    "run_1",
    "--command-id",
    "command_1",
)


def _parsed(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


def test_parser_accepts_factory_before_init_without_loading_it() -> None:
    args = _parsed(
        "--factory",
        "package.module:create_cli_application",
        "init",
        "workspace",
    )

    assert vars(args) == {
        "factory": "package.module:create_cli_application",
        "command": "init",
        "path": "workspace",
    }


def test_parser_accepts_run_submit_with_exact_inputs() -> None:
    args = _parsed(
        "run",
        "submit",
        *_SCOPE,
        "--submission-id",
        "submission_1",
        "--content-file",
        "prompt.txt",
        "--artifact",
        "art_1",
        "--artifact",
        "art_2",
    )

    assert vars(args) == {
        "factory": None,
        "command": "run",
        "action": "submit",
        "tenant": "tenant_1",
        "session_id": "session_1",
        "submission_id": "submission_1",
        "content": None,
        "content_file": "prompt.txt",
        "stdin": False,
        "artifact": ["art_1", "art_2"],
    }


@pytest.mark.parametrize("kind", ["hitl_answer", "resolve_side_effect"])
def test_parser_requires_payload_for_payload_bearing_commands(kind: str) -> None:
    with pytest.raises(ValueError, match="invalid CLI input"):
        _parsed(*_COMMAND, "--kind", kind)


@pytest.mark.parametrize("kind", ["cancel", "resume", "wakeup", "retry"])
def test_parser_normalizes_missing_optional_command_payload(kind: str) -> None:
    args = _parsed(*_COMMAND, "--kind", kind)

    assert args.payload_json == "{}"
    assert args.payload_file is None


def test_parser_accepts_command_payload_file() -> None:
    args = _parsed(
        *_COMMAND,
        "--kind",
        "resolve_side_effect",
        "--payload-file",
        "resolution.json",
    )

    assert args.payload_json is None
    assert args.payload_file == "resolution.json"


def test_parser_leaves_unfrozen_numeric_defaults_to_command_owners() -> None:
    artifact_args = _parsed("artifact", "list", *_SCOPE)
    worker_args = _parsed("worker", "start")
    relay_args = _parsed("relay", "start")

    assert (artifact_args.cursor, artifact_args.limit) == (None, None)
    assert worker_args.drain_timeout is None
    assert relay_args.idle_interval is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ("run", "get", *_SCOPE, "--run-id", "run_1"),
            {"command": "run", "action": "get", "run_id": "run_1"},
        ),
        (
            (
                "run",
                "watch",
                *_SCOPE,
                "--run-id",
                "run_1",
                "--cursor",
                "cursor_1",
            ),
            {"command": "run", "action": "watch", "cursor": "cursor_1"},
        ),
        (
            (
                "artifact",
                "upload",
                *_SCOPE,
                "--upload-id",
                "upload_1",
                "--file",
                "drawing.png",
                "--media-type",
                "image/png",
            ),
            {"command": "artifact", "action": "upload", "file": "drawing.png"},
        ),
        (
            (
                "artifact",
                "list",
                *_SCOPE,
                "--cursor",
                "cursor_1",
                "--limit",
                "25",
            ),
            {"command": "artifact", "action": "list", "limit": 25},
        ),
        (
            (
                "artifact",
                "read",
                *_SCOPE,
                "--artifact-id",
                "art_1",
                "--output",
                "-",
            ),
            {"command": "artifact", "action": "read", "output": "-"},
        ),
        (
            (
                "artifact",
                "delete",
                *_SCOPE,
                "--artifact-id",
                "art_1",
                "--deletion-id",
                "delete_1",
            ),
            {"command": "artifact", "action": "delete", "deletion_id": "delete_1"},
        ),
        (
            ("worker", "start", "--drain-timeout", "12.5"),
            {"command": "worker", "action": "start", "drain_timeout": 12.5},
        ),
        (
            ("relay", "start", "--idle-interval", "0.25"),
            {"command": "relay", "action": "start", "idle_interval": 0.25},
        ),
        (("serve",), {"command": "serve"}),
        (("migrate", "--check"), {"command": "migrate", "check": True}),
    ],
)
def test_parser_accepts_canonical_command_tree(
    argv: tuple[str, ...],
    expected: dict[str, object],
) -> None:
    args = _parsed(*argv)

    for field, value in expected.items():
        assert getattr(args, field) == value


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("run",),
        ("artifact",),
        ("worker",),
        ("relay",),
        ("run", "package.module:app"),
        ("migrate", "--dsn", "postgresql://example"),
        ("migrate", "--dry-run"),
        ("serve", "--principal", "admin"),
        ("--fact", "package.module:create", "serve"),
        (
            "run",
            "get",
            "--ten",
            "tenant_1",
            "--session-id",
            "s1",
            "--run-id",
            "r1",
        ),
        ("init", "workspace", "--factory", "package.module:create"),
    ],
)
def test_parser_rejects_missing_commands_and_legacy_inputs(
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="invalid CLI input"):
        _parsed(*argv)


@pytest.mark.parametrize(
    "argv",
    [
        ("run", "submit", *_SCOPE, "--submission-id", "submission_1"),
        (
            "run",
            "submit",
            *_SCOPE,
            "--submission-id",
            "submission_1",
            "--content",
            "hello",
            "--stdin",
        ),
        (
            *_COMMAND,
            "--kind",
            "cancel",
            "--payload-json",
            "{}",
            "--payload-file",
            "payload.json",
        ),
        (*_COMMAND, "--kind", "unknown"),
        ("run", "get", *_SCOPE, "--run-id", "run_1", "--principal", "admin"),
        ("artifact", "list", *_SCOPE, "--limit", "0"),
        ("worker", "start", "--drain-timeout", "-1"),
        ("worker", "start", "--drain-timeout", "nan"),
        ("relay", "start", "--idle-interval", "0"),
        ("relay", "start", "--idle-interval", "inf"),
    ],
)
def test_parser_reports_usage_errors_with_exit_code_two(
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="invalid CLI input"):
        _parsed(*argv)
