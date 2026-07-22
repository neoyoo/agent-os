from __future__ import annotations

from io import BytesIO, StringIO
import json
import sys

import pytest

from agentos.cli.output import (
    write_error,
    write_json_line,
    write_jsonl_record,
    write_raw_bytes,
)


def test_json_line_is_compact_sorted_unicode_and_exactly_one_line() -> None:
    stdout = StringIO()

    write_json_line({"z": 1, "message": "图纸"}, stream=stdout)

    assert stdout.getvalue() == '{"message":"图纸","z":1}\n'


def test_json_line_defaults_to_stdout_only(capsys) -> None:
    write_json_line({"ok": True})

    captured = capsys.readouterr()
    assert captured.out == '{"ok":true}\n'
    assert captured.err == ""


def test_jsonl_record_writes_one_canonical_record() -> None:
    stdout = StringIO()

    write_jsonl_record({"type": "delta", "sequence": 2}, stream=stdout)

    assert stdout.getvalue() == '{"sequence":2,"type":"delta"}\n'


@pytest.mark.parametrize("writer", [write_json_line, write_jsonl_record])
def test_json_output_rejects_nan_before_writing(writer) -> None:  # type: ignore[no-untyped-def]
    stdout = StringIO()

    with pytest.raises(ValueError, match="Out of range float values"):
        writer({"value": float("nan")}, stream=stdout)

    assert stdout.getvalue() == ""


def test_error_defaults_to_only_compact_stderr_line(capsys) -> None:
    write_error("not_found", "未找到")

    captured = capsys.readouterr()
    assert captured.err == '{"code":"not_found","message":"未找到"}\n'
    assert captured.out == ""


def test_raw_bytes_are_not_json_encoded_or_newline_terminated(monkeypatch) -> None:
    stdout = BytesIO()

    def fail_json_encoding(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("raw output must not use JSON encoding")

    monkeypatch.setattr(json, "dumps", fail_json_encoding)

    write_raw_bytes(b'raw\x00{"value":1}', stream=stdout)

    assert stdout.getvalue() == b'raw\x00{"value":1}'


def test_raw_bytes_default_to_stdout_binary_buffer(monkeypatch) -> None:
    class BinaryStdout:
        def __init__(self) -> None:
            self.buffer = BytesIO()

    stdout = BinaryStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    write_raw_bytes(b"artifact")

    assert stdout.buffer.getvalue() == b"artifact"
