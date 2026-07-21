from __future__ import annotations

import json
import math


_MAX_SAFE_INTEGER = 2**53 - 1


def jcs_bytes(value: object) -> bytes:
    return _encode(value).encode("utf-8")


def _encode(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is str:
        _reject_surrogates(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is int:
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise ValueError("JCS integer exceeds the IEEE-754 safe domain")
        return str(value)
    if type(value) is float:
        return _encode_float(value)
    if type(value) is list:
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("JCS object keys must be strings")
        keys = sorted(value, key=_utf16_sort_key)
        return (
            "{"
            + ",".join(f"{_encode(key)}:{_encode(value[key])}" for key in keys)
            + "}"
        )
    raise TypeError("value is not valid JCS JSON")


def _encode_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("JCS numbers must be finite")
    if value == 0:
        return "0"
    if value < 0:
        return "-" + _encode_float(-value)

    serialized = str(value)
    exponent = ""
    exponent_value = 0
    exponent_at = serialized.find("e")
    if exponent_at > 0:
        exponent = serialized[exponent_at:]
        if exponent[2:3] == "0":
            exponent = exponent[:2] + exponent[3:]
        serialized = serialized[:exponent_at]
        exponent_value = int(exponent[1:])

    first = serialized
    dot = ""
    last = ""
    dot_at = serialized.find(".")
    if dot_at > 0:
        first = serialized[:dot_at]
        dot = "."
        last = serialized[dot_at + 1 :]
    if last == "0":
        dot = ""
        last = ""

    if 0 < exponent_value < 21:
        first += last
        last = ""
        dot = ""
        exponent = ""
        missing = exponent_value - len(first)
        while missing >= 0:
            first += "0"
            missing -= 1
    elif -7 < exponent_value < 0:
        last = first + last
        first = "0"
        dot = "."
        exponent = ""
        missing = exponent_value
        while missing < -1:
            last = "0" + last
            missing += 1
    return first + dot + last + exponent


def _utf16_sort_key(value: str) -> bytes:
    _reject_surrogates(value)
    return value.encode("utf-16-be")


def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("JCS strings must not contain lone surrogates")


__all__ = ["jcs_bytes"]
