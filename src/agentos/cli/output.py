from __future__ import annotations

import json
import sys
from typing import BinaryIO, TextIO


def write_json_line(value: object, *, stream: TextIO | None = None) -> None:
    """向标准输出或注入流写入一行 canonical JSON。"""

    _write_json_record(value, stream=sys.stdout if stream is None else stream)


def write_jsonl_record(value: object, *, stream: TextIO | None = None) -> None:
    """向标准输出或注入流写入一条 canonical JSONL 记录。"""

    _write_json_record(value, stream=sys.stdout if stream is None else stream)


def write_error(code: str, message: str, *, stream: TextIO | None = None) -> None:
    """向标准错误或注入流写入一条稳定的 CLI 错误记录。"""

    _write_json_record(
        {"code": code, "message": message},
        stream=sys.stderr if stream is None else stream,
    )


def write_raw_bytes(value: bytes, *, stream: BinaryIO | None = None) -> None:
    """写入未经编码且不追加换行的 Artifact 原始字节。"""

    target = sys.stdout.buffer if stream is None else stream
    target.write(value)
    target.flush()


def _write_json_record(value: object, *, stream: TextIO) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    stream.write(encoded)
    stream.write("\n")
    stream.flush()


__all__ = [
    "write_error",
    "write_json_line",
    "write_jsonl_record",
    "write_raw_bytes",
]
