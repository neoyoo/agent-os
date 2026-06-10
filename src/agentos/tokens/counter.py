from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
import json
from math import ceil
from typing import Protocol


class TokenCounter(Protocol):
    """模型无关的 token 估算边界。"""

    def count_text(self, text: str) -> int:
        """估算一段文本的 token 数。"""

    def count_messages(
        self,
        messages: Sequence[object],
        tools: Sequence[object] | None = None,
    ) -> int:
        """估算消息和工具 schema 的 token 数。"""


@dataclass(frozen=True, slots=True)
class HeuristicTokenCounter:
    """默认启发式 token counter，优先可选 tiktoken，失败则按字符估算。"""

    model: str | None = None
    char_per_token: float = 4.0

    def __post_init__(self) -> None:
        """校验启发式参数。"""

        if self.char_per_token <= 0:
            raise ValueError("char_per_token must be greater than 0")

    def count_text(self, text: str) -> int:
        """估算一段文本的 token 数。"""

        if not text:
            return 0
        encoded_count = self._count_with_tiktoken(text)
        if encoded_count is not None:
            return encoded_count
        return ceil(len(text) / self.char_per_token)

    def count_messages(
        self,
        messages: Sequence[object],
        tools: Sequence[object] | None = None,
    ) -> int:
        """估算消息和工具 schema 的 token 数。"""

        parts = [self._json_text(message) for message in messages]
        if tools:
            parts.extend(self._json_text(tool) for tool in tools)
        return self.count_text("\n".join(part for part in parts if part))

    def _count_with_tiktoken(self, text: str) -> int | None:
        if self.model is None:
            return None
        lowered = self.model.lower()
        if not any(prefix in lowered for prefix in ("gpt", "o1", "o3", "o4")):
            return None
        try:
            import tiktoken  # type: ignore[import-not-found]
        except Exception:
            return None
        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))

    def _json_text(self, value: object) -> str:
        safe_value = self._json_safe(value)
        return json.dumps(safe_value, ensure_ascii=False, sort_keys=True)

    def _json_safe(self, value: object) -> object:
        to_provider_dict = getattr(value, "to_provider_dict", None)
        if callable(to_provider_dict):
            return self._json_safe(to_provider_dict())
        if is_dataclass(value):
            return self._json_safe(asdict(value))
        if isinstance(value, Mapping):
            return {
                str(key): self._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
