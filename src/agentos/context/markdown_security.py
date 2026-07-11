"""SystemEnvelope Markdown 一级标题的结构安全适配层。"""

from __future__ import annotations

from collections.abc import Iterable
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agentos.context.models import ContextProtocolError

_PARSER_FAILED = "system section markdown parser failed"


def normalize_markdown_text(text: str) -> str:
    """保留 Markdown 缩进并规范化首尾空白行与 UTF-8。"""

    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    normalized = "\n".join(lines[start:end])
    invalid_utf8 = False
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        invalid_utf8 = True
    if invalid_utf8:
        raise ContextProtocolError("system section body is not valid UTF-8")
    return normalized


def validate_no_h1(text: str) -> None:
    """拒绝 CommonMark token tree 中 Renderer 之外的 H1。"""

    for token in _walk_tokens(_parse(text)):
        if token.type == "heading_open" and token.tag == "h1":
            raise ContextProtocolError("system section body contains reserved heading")
        if token.type in ("html_block", "html_inline") and _html_contains_h1(
            token.content,
        ):
            raise ContextProtocolError("system section body contains reserved heading")


def generated_h1_is_safe(title: str) -> bool:
    """验证 Renderer 构造的单个 H1 未被 title 中的 raw HTML 打断。"""

    if _html_contains_h1(title):
        return False
    tokens = _parse(f"# {title}")
    if [(token.type, token.tag) for token in tokens] != [
        ("heading_open", "h1"),
        ("inline", ""),
        ("heading_close", "h1"),
    ]:
        return False
    return not any(
        token.type in ("html_block", "html_inline")
        and _html_contains_h1(token.content)
        for token in _walk_tokens(tokens)
    )


def _parse(text: str) -> list[Token]:
    failed = False
    try:
        tokens = MarkdownIt("commonmark", {"html": True}).parse(text)
    except Exception:
        failed = True
        tokens = []
    if failed:
        raise ContextProtocolError(_PARSER_FAILED)
    return tokens


def _walk_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk_tokens(token.children)


def _html_contains_h1(text: str) -> bool:
    failed = False
    detector: _H1HTMLParser | None = None
    try:
        detector = _H1HTMLParser()
        detector.feed(text)
        detector.close()
    except Exception:
        failed = True
    if failed or detector is None:
        raise ContextProtocolError(_PARSER_FAILED)
    return detector.contains_h1


class _H1HTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contains_h1 = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag)

    def handle_endtag(self, tag: str) -> None:
        self._record(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._record(tag)

    def _record(self, tag: str) -> None:
        if tag.casefold() == "h1":
            self.contains_h1 = True
