"""Provider 无关的不可变逻辑输入类型。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import InitVar, dataclass
from typing import Literal, TypeAlias

from agentos._internal_transcript import InternalTranscriptValue
from agentos._json_values import FrozenJsonObject, freeze_json
from agentos.providers.content import (
    FilePart,
    ImagePart,
    ProviderContentPart,
    TextPart,
)


ProviderRole: TypeAlias = Literal["user", "assistant", "tool"]
ProviderInputKind: TypeAlias = Literal[
    "context_snapshot",
    "business_message",
    "tool_result",
    "recalled_message",
    "model_task",
    "context_mount",
]
InputOrigin: TypeAlias = Literal[
    "runtime",
    "message_store",
    "recall_runtime",
    "artifact_runtime",
]
InputAuthority: TypeAlias = Literal[
    "context_data",
    "conversation_data",
    "tool_data",
    "artifact_data",
]
PersistencePolicy: TypeAlias = Literal["stored", "ephemeral"]
VisibilityPolicy: TypeAlias = Literal["conversation", "internal"]


_MODEL_TASK_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class ProviderToolCall:
    """Provider 边界中的深不可变工具调用。"""

    id: str
    name: str
    arguments: FrozenJsonObject

    def __init__(
        self,
        id: str,
        name: str,
        arguments: dict[str, object] | FrozenJsonObject | None = None,
    ) -> None:
        frozen = freeze_json({} if arguments is None else arguments)
        if not isinstance(frozen, FrozenJsonObject):
            raise TypeError("provider tool call arguments must be a JSON object")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class ProviderInputItem(InternalTranscriptValue):
    """一次 Provider 请求中的临时逻辑输入。"""

    role: ProviderRole
    kind: ProviderInputKind
    origin: InputOrigin
    authority: InputAuthority
    persistence: PersistencePolicy
    visibility: VisibilityPolicy
    content: tuple[ProviderContentPart, ...]
    tool_calls: tuple[ProviderToolCall, ...] = ()
    tool_call_id: str | None = None
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        """冻结集合并校验完整元数据矩阵。"""

        if self.kind == "model_task" and _factory_token is not _MODEL_TASK_FACTORY_TOKEN:
            raise ValueError("model_task provider input requires model_task() factory")
        content = _normalize_content(self.content)
        tool_calls = tuple(self.tool_calls)
        if any(type(item) is not ProviderToolCall for item in tool_calls):
            raise TypeError("provider input tool_calls require ProviderToolCall")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "tool_calls", tool_calls)
        _validate_provider_input_item(self)

    @classmethod
    def context_snapshot(cls, xml: str) -> ProviderInputItem:
        """创建固定权限的 ContextSnapshot 输入。"""

        return cls(
            role="user",
            kind="context_snapshot",
            origin="runtime",
            authority="context_data",
            persistence="ephemeral",
            visibility="internal",
            content=(TextPart(xml),),
        )

    @classmethod
    def business_user(cls, content: str) -> ProviderInputItem:
        """创建持久业务 user 输入。"""

        return cls._business("user", content)

    @classmethod
    def business_assistant(
        cls,
        content: str,
        tool_calls: Iterable[ProviderToolCall] = (),
    ) -> ProviderInputItem:
        """创建持久业务 assistant 输入。"""

        return cls._business("assistant", content, tool_calls)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> ProviderInputItem:
        """创建已落库的 Tool Result 输入。"""

        return cls(
            role="tool",
            kind="tool_result",
            origin="message_store",
            authority="tool_data",
            persistence="stored",
            visibility="internal",
            content=(TextPart(content),),
            tool_call_id=tool_call_id,
        )

    @classmethod
    def recalled_user(cls, content: str) -> ProviderInputItem:
        """创建临时召回的 user 输入。"""

        return cls._recalled("user", content)

    @classmethod
    def recalled_assistant(
        cls,
        content: str,
        tool_calls: Iterable[ProviderToolCall] = (),
    ) -> ProviderInputItem:
        """创建临时召回的 assistant 输入。"""

        return cls._recalled("assistant", content, tool_calls)

    @classmethod
    def recalled_tool(
        cls,
        tool_call_id: str,
        content: str,
    ) -> ProviderInputItem:
        """创建临时召回的 Tool Result 输入。"""

        return cls(
            role="tool",
            kind="recalled_message",
            origin="recall_runtime",
            authority="tool_data",
            persistence="ephemeral",
            visibility="internal",
            content=(TextPart(content),),
            tool_call_id=tool_call_id,
        )

    @classmethod
    def model_task(cls, text: str) -> ProviderInputItem:
        """创建 Runtime 内部模型任务的不可信文本输入。"""

        if not isinstance(text, str):
            raise TypeError("model_task text must be str")
        return cls(
            role="user",
            kind="model_task",
            origin="runtime",
            authority="context_data",
            persistence="ephemeral",
            visibility="internal",
            content=(TextPart(text),),
            _factory_token=_MODEL_TASK_FACTORY_TOKEN,
        )

    @classmethod
    def context_mount(
        cls,
        content: Iterable[ProviderContentPart],
    ) -> ProviderInputItem:
        """创建当前 Turn 的 Artifact 内容挂载输入。"""

        return cls(
            role="user",
            kind="context_mount",
            origin="artifact_runtime",
            authority="artifact_data",
            persistence="ephemeral",
            visibility="internal",
            content=tuple(content),
        )

    @classmethod
    def _business(
        cls,
        role: Literal["user", "assistant"],
        content: str,
        tool_calls: Iterable[ProviderToolCall] = (),
    ) -> ProviderInputItem:
        return cls(
            role=role,
            kind="business_message",
            origin="message_store",
            authority="conversation_data",
            persistence="stored",
            visibility="conversation",
            content=(TextPart(content),),
            tool_calls=tuple(tool_calls),
        )

    @classmethod
    def _recalled(
        cls,
        role: Literal["user", "assistant"],
        content: str,
        tool_calls: Iterable[ProviderToolCall] = (),
    ) -> ProviderInputItem:
        return cls(
            role=role,
            kind="recalled_message",
            origin="recall_runtime",
            authority="conversation_data",
            persistence="ephemeral",
            visibility="internal",
            content=(TextPart(content),),
            tool_calls=tuple(tool_calls),
        )


_ALLOWED_METADATA = frozenset(
    {
        ("user", "context_snapshot", "runtime", "context_data", "ephemeral", "internal"),
        ("user", "business_message", "message_store", "conversation_data", "stored", "conversation"),
        ("assistant", "business_message", "message_store", "conversation_data", "stored", "conversation"),
        ("tool", "tool_result", "message_store", "tool_data", "stored", "internal"),
        ("user", "recalled_message", "recall_runtime", "conversation_data", "ephemeral", "internal"),
        ("assistant", "recalled_message", "recall_runtime", "conversation_data", "ephemeral", "internal"),
        ("tool", "recalled_message", "recall_runtime", "tool_data", "ephemeral", "internal"),
        ("user", "model_task", "runtime", "context_data", "ephemeral", "internal"),
        ("user", "context_mount", "artifact_runtime", "artifact_data", "ephemeral", "internal"),
    },
)


def _normalize_content(
    content: Iterable[ProviderContentPart],
) -> tuple[ProviderContentPart, ...]:
    if isinstance(content, (str, bytes)):
        raise TypeError("provider input content parts require a sequence")
    normalized = tuple(content)
    if any(type(item) not in (TextPart, ImagePart, FilePart) for item in normalized):
        raise TypeError("provider input content parts contain an unsupported value")
    return normalized


def _validate_provider_input_item(item: ProviderInputItem) -> None:
    metadata = (
        item.role,
        item.kind,
        item.origin,
        item.authority,
        item.persistence,
        item.visibility,
    )
    if metadata not in _ALLOWED_METADATA:
        raise ValueError("provider input metadata matrix violation")
    if item.kind == "model_task" and (
        len(item.content) != 1 or type(item.content[0]) is not TextPart
    ):
        raise ValueError("model_task provider input requires exactly one TextPart")
    if item.role == "tool":
        if not isinstance(item.tool_call_id, str) or not item.tool_call_id:
            raise ValueError("tool provider input requires tool_call_id")
    elif item.tool_call_id is not None:
        raise ValueError("non-tool provider input cannot define tool_call_id")
    if item.role != "assistant" and item.tool_calls:
        raise ValueError("provider input tool_calls require assistant role")
