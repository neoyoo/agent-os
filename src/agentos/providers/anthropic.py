import base64
from dataclasses import dataclass
from typing import Any

from agentos.attachments.types import (
    BytesSource,
    InlineBase64Source,
    LocalFileSource,
    ProviderFileSource,
    UrlSource,
)
from agentos.providers.base import (
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
    ProviderUsage,
)
from agentos.providers.messages import (
    AssistantMessage,
    FilePart,
    ImagePart,
    ProviderContentPart,
    ToolResultMessage,
    TextPart,
    UserMessage,
    provider_message_to_dict,
)


@dataclass(slots=True)
class AnthropicProvider:
    """Anthropic messages 薄适配器，client 由调用方注入。"""

    client: Any
    model: str
    max_tokens: int = 4096

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """调用注入的 Anthropic client，并标准化响应。"""

        self._ensure_no_active_system_messages(request)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=request.system,
            messages=self._messages(request.messages),
            tools=self._tools(request.tools) or None,
        )
        text_parts: list[str] = []
        tool_calls: list[ProviderToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ProviderToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input),
                    ),
                )
        return ProviderResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
            usage=self._usage(getattr(response, "usage", None)),
            model=getattr(response, "model", None) or self.model,
            provider_name="anthropic",
            response_id=getattr(response, "id", None),
        )

    def _ensure_no_active_system_messages(self, request: ProviderRequest) -> None:
        """拒绝 active window 中的 system 消息，避免 provider 收到双 system。"""

        for message in request.messages:
            if not isinstance(
                message,
                (UserMessage, AssistantMessage, ToolResultMessage),
            ):
                raise ValueError(
                    "active messages must not include system role; use "
                    "ProviderRequest.system",
                )

    def _message(self, message: ProviderMessage) -> dict[str, object]:
        """把 provider message 转为 Anthropic Messages API 形态。"""

        if isinstance(message, UserMessage):
            return {
                "role": "user",
                "content": self._user_content(message.content),
            }
        if isinstance(message, AssistantMessage):
            content: list[dict[str, object]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for tool_call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.arguments,
                    },
                )
            return {
                "role": "assistant",
                "content": content if content else message.content,
            }
        if isinstance(message, ToolResultMessage):
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content,
                    },
                ],
            }

        return provider_message_to_dict(message)

    def _user_content(self, content: object) -> object:
        """把 canonical content parts 转为 Anthropic content blocks。"""

        if isinstance(content, str):
            return content
        if isinstance(content, tuple):
            return [self._content_part(part) for part in content]
        return content

    def _content_part(self, part: ProviderContentPart) -> dict[str, object]:
        """把单个 canonical part 转为 Anthropic content block。"""

        if isinstance(part, TextPart):
            return {"type": "text", "text": part.text}
        if isinstance(part, ImagePart):
            return {
                "type": "image",
                "source": self._source_block(part.attachment, require_image=True),
            }
        if isinstance(part, FilePart):
            mime_type = str(getattr(part.attachment, "mime_type", ""))
            if mime_type != "application/pdf":
                raise ValueError("Anthropic file attachments only support PDF in v1")
            return {
                "type": "document",
                "source": self._source_block(part.attachment),
            }
        raise ValueError(f"unsupported Anthropic content part: {type(part).__name__}")

    def _source_block(
        self,
        attachment: object,
        *,
        require_image: bool = False,
    ) -> dict[str, object]:
        """把附件 source 转为 Anthropic source block。"""

        mime_type = str(getattr(attachment, "mime_type", ""))
        if require_image and not mime_type.startswith("image/"):
            raise ValueError("Anthropic image parts require image MIME")
        source = getattr(attachment, "source", None)
        if isinstance(source, UrlSource):
            return {"type": "url", "url": source.url}
        if isinstance(source, InlineBase64Source):
            return {
                "type": "base64",
                "media_type": source.mime_type,
                "data": source.data,
            }
        if isinstance(source, BytesSource):
            return {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.b64encode(source.data).decode("ascii"),
            }
        if isinstance(source, LocalFileSource):
            return {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.b64encode(source.path.read_bytes()).decode("ascii"),
            }
        if isinstance(source, ProviderFileSource):
            if source.state != "ready":
                raise ValueError(f"provider file is not ready: {source.state}")
            return {"type": "file", "file_id": source.file_id}
        raise ValueError("unsupported Anthropic attachment source")

    def _messages(self, messages: list[ProviderMessage]) -> list[dict[str, object]]:
        """转换并合并连续 tool_result，满足 Anthropic 角色交替规则。"""

        return self._merge_consecutive_tool_results(
            [self._message(message) for message in messages],
        )

    def _merge_consecutive_tool_results(
        self,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """把连续 tool_result user blocks 合并为一条 user 消息。"""

        merged: list[dict[str, object]] = []
        for message in messages:
            if (
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and merged
                and merged[-1].get("role") == "user"
                and isinstance(merged[-1].get("content"), list)
            ):
                merged[-1]["content"].extend(message["content"])  # type: ignore[union-attr]
                continue
            merged.append(message)
        return merged

    def _tools(self, tools: list[ProviderToolSpec]) -> list[dict[str, object]]:
        """把内部 function tool schema 转成 Anthropic input_schema 形态。"""

        converted: list[dict[str, object]] = []
        for tool in tools:
            converted.append(
                {
                    "name": tool.function.name,
                    "description": tool.function.description,
                    "input_schema": tool.function.parameters,
                },
            )
        return converted

    def _usage(self, raw_usage: object | None) -> ProviderUsage | None:
        """把 Anthropic usage 标准化。"""

        if raw_usage is None:
            return None
        return ProviderUsage(
            input_tokens=getattr(raw_usage, "input_tokens", None),
            output_tokens=getattr(raw_usage, "output_tokens", None),
            cached_input_tokens=getattr(raw_usage, "cache_read_input_tokens", None),
            cache_creation_input_tokens=getattr(
                raw_usage,
                "cache_creation_input_tokens",
                None,
            ),
        )
