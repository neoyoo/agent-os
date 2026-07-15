"""OpenAI-compatible HTTP、SSE 与 timeout transport。"""

from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator, Iterator
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentos.providers.base import ProviderTimeoutError


class OpenAICompatibleProviderError(RuntimeError):
    """OpenAI-compatible provider 请求失败。"""


_STREAM_DONE = object()


def _parse_openai_stream_line(line: str) -> dict[str, object] | object | None:
    """解析单行 OpenAI-compatible SSE，忽略 SSE 控制字段。"""

    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if ":" in line and line.split(":", 1)[0] in {"event", "id", "retry"}:
        return None
    if line.startswith("data:"):
        line = line.removeprefix("data:").strip()
    if not line:
        return None
    if line == "[DONE]":
        return _STREAM_DONE
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as error:
        raise OpenAICompatibleProviderError(
            "OpenAI-compatible stream chunk is not valid JSON: " + line[:200],
        ) from error
    if not isinstance(parsed, dict):
        raise ValueError("stream chunk must be a JSON object")
    return parsed


class OpenAICompatibleTransport(Protocol):
    """OpenAI-compatible JSON HTTP transport。"""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]: ...

    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Iterator[dict[str, object]]: ...


class AsyncOpenAICompatibleTransport(Protocol):
    """OpenAI-compatible async JSON HTTP transport。"""

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]: ...

    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> AsyncIterator[dict[str, object]]: ...


class UrlLibJSONTransport:
    """基于标准库 urllib 的 JSON transport。"""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        request = Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = response.read().decode("utf-8")
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed with HTTP {error.code}: {body}",
            ) from error
        except URLError as error:
            self._map_transport_error(error)
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error.reason}",
            ) from error
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI-compatible response must be a JSON object")
        return parsed

    def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> Iterator[dict[str, object]]:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        request = Request(
            url=url,
            data=json.dumps(stream_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                for raw_line in response:
                    parsed = _parse_openai_stream_line(raw_line.decode("utf-8"))
                    if parsed is None:
                        continue
                    if parsed is _STREAM_DONE:
                        break
                    yield parsed
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed with HTTP {error.code}: {body}",
            ) from error
        except URLError as error:
            self._map_transport_error(error)
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error.reason}",
            ) from error

    def _map_transport_error(self, error: URLError) -> None:
        reason = getattr(error, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise ProviderTimeoutError(
                "OpenAI-compatible request timed out",
            ) from error


class HttpxAsyncJSONTransport:
    """基于可选 httpx 依赖的 async JSON transport。"""

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        httpx = self._httpx()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                parsed = response.json()
        except httpx.HTTPStatusError as error:
            body = await _async_response_error_text(error.response)
            raise OpenAICompatibleProviderError(
                "OpenAI-compatible request failed with HTTP "
                f"{error.response.status_code}: {body}",
            ) from error
        except httpx.HTTPError as error:
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error}",
            ) from error
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI-compatible response must be a JSON object")
        return parsed

    async def post_json_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> AsyncIterator[dict[str, object]]:
        httpx = self._httpx()
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=stream_payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        parsed = _parse_openai_stream_line(line)
                        if parsed is None:
                            continue
                        if parsed is _STREAM_DONE:
                            break
                        yield parsed
        except httpx.HTTPStatusError as error:
            body = await _async_response_error_text(error.response)
            raise OpenAICompatibleProviderError(
                "OpenAI-compatible request failed with HTTP "
                f"{error.response.status_code}: {body}",
            ) from error
        except httpx.HTTPError as error:
            raise OpenAICompatibleProviderError(
                f"OpenAI-compatible request failed: {error}",
            ) from error

    def _httpx(self) -> object:
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - environment dependent
            raise OpenAICompatibleProviderError(
                "async OpenAI-compatible transport requires installing "
                "agent-os[async-http]",
            ) from error
        return httpx


async def _async_response_error_text(response: object) -> str:
    aread = getattr(response, "aread", None)
    if callable(aread):
        try:
            await aread()
        except Exception:
            pass
    try:
        return str(getattr(response, "text"))
    except Exception:
        return ""
