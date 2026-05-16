import re
from typing import Sequence

from agentos.messages import Message


def clip_text(value: str, *, limit: int) -> str:
    """按字符上限裁剪文本，保留词间空白归一化。"""

    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def extract_keywords(messages: Sequence[Message]) -> tuple[str, ...]:
    """从源消息中提取适合词法检索的稳定关键词。"""

    keywords: list[str] = []
    for message in messages:
        for token in re.findall(r"[A-Za-z0-9_./:-]+", message.content):
            if _is_keyword(token):
                keywords.append(token.strip(".,;:"))
        for tool_call in message.tool_calls:
            keywords.append(tool_call.name)
            for value in tool_call.arguments.values():
                if isinstance(value, str):
                    keywords.extend(
                        token.strip(".,;:")
                        for token in re.findall(r"[A-Za-z0-9_./:-]+", value)
                        if _is_keyword(token)
                    )
    return _dedupe(keywords)


def extract_tool_hints(messages: Sequence[Message]) -> tuple[str, ...]:
    """提取工具调用名称和关键参数摘要。"""

    hints: list[str] = []
    for message in messages:
        for tool_call in message.tool_calls:
            path = tool_call.arguments.get("path")
            if isinstance(path, str):
                hints.append(f"{tool_call.name}(path={path})")
            else:
                hints.append(tool_call.name)
    return _dedupe(hints)


def build_searchable_text(
    messages: Sequence[Message],
    *,
    max_items: int,
    limit: int,
) -> str:
    """生成 recall index 使用的短检索文本。"""

    text = " ".join(
        f"{message.role}: {clip_text(message.content, limit=120)}"
        for message in messages[:max_items]
    )
    return clip_text(text, limit=limit)


def _is_keyword(token: str) -> bool:
    return "." in token or "-" in token or "_" in token or len(token) >= 4


def _dedupe(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)
