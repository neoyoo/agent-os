"""ContextSnapshot 投影中的敏感表示校验。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

from agentos.context.models import ContextSensitiveDataError
from agentos.context.xml import XmlElement

_DATA_URL_PATTERN = re.compile(
    r"(?:^|[\s\"'=])data:[^,\s]*;base64,",
    re.IGNORECASE,
)
_FILE_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])file[-_][A-Za-z0-9]{20,}(?![A-Za-z0-9])",
)
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PATH_SEPARATOR_PATTERN = re.compile(r"[\s\"']+")
_WINDOWS_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH_PATTERN = re.compile(r"^\\\\[^\\/\s]+[\\/][^\\/\s]+")
_POSIX_PATH_PATTERN = re.compile(r"^/(?:[^/\s\x00]+/)+[^/\s\x00]*$")


class SensitiveRepresentationValidator:
    """递归拒绝不得进入默认 ContextSnapshot 的敏感表示。"""

    __slots__ = ()

    def validate(self, value: object, *, slot: str) -> None:
        """检查 typed value 或 XmlElement，错误只暴露类别与 Slot。"""

        if isinstance(value, str):
            self._validate_string(value, slot)
        elif isinstance(value, XmlElement):
            if value.text is not None:
                self._validate_string(value.text, slot)
            for _, attribute_value in value.attributes:
                self._validate_string(attribute_value, slot)
            for child in value.children:
                self.validate(child, slot=slot)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                self.validate(key, slot=slot)
                self.validate(item, slot=slot)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.validate(item, slot=slot)

    def _validate_string(self, value: str, slot: str) -> None:
        if _DATA_URL_PATTERN.search(value):
            self._reject("data-url", slot)
        if _FILE_ID_PATTERN.search(value):
            self._reject("provider-file-id", slot)
        if any(_is_signed_url(url) for url in _URL_PATTERN.findall(value)):
            self._reject("signed-url", slot)
        candidates = (value.strip(), *_PATH_SEPARATOR_PATTERN.split(value))
        if any(_is_absolute_path(item) for item in candidates if item):
            self._reject("absolute-path", slot)

    @staticmethod
    def _reject(category: str, slot: str) -> None:
        raise ContextSensitiveDataError(
            f"sensitive representation category={category} slot={slot}",
        )


def _is_signed_url(value: str) -> bool:
    try:
        query = parse_qs(urlsplit(value).query, keep_blank_values=True)
    except ValueError:
        return False
    keys = {key.casefold() for key in query}
    if "x-amz-signature" in keys or "x-goog-signature" in keys:
        return True
    if {"signature", "googleaccessid"} <= keys:
        return True
    return "sig" in keys and "sv" in keys and bool({"se", "sp"} & keys)


def _is_absolute_path(value: str) -> bool:
    if value.casefold().startswith(("http://", "https://")):
        return False
    return bool(
        _WINDOWS_PATH_PATTERN.match(value)
        or _UNC_PATH_PATTERN.match(value)
        or _POSIX_PATH_PATTERN.fullmatch(value)
    )
