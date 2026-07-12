"""StoredMessage 到 ProviderInputItem 的纯投影。"""

from agentos.messages import MessageRef, StoredMessage, ToolCall
from agentos.providers import ProviderInputItem, ProviderToolCall


def project_tool_call(tool_call: ToolCall) -> ProviderToolCall:
    """把业务工具调用投影为 Provider 无关工具调用。"""

    return ProviderToolCall(
        id=tool_call.id,
        name=tool_call.name,
        arguments=tool_call.arguments,
    )


def project_stored_message(
    ref: MessageRef,
    message: StoredMessage,
) -> ProviderInputItem:
    """按 active ref 的 temporary 属性投影一条业务消息。"""

    _validate_stored_message_shape(message)
    tool_calls = tuple(project_tool_call(item) for item in message.tool_calls)
    if message.role == "user":
        if ref.temporary:
            return ProviderInputItem.recalled_user(message.content)
        return ProviderInputItem.business_user(message.content)
    if message.role == "assistant":
        if ref.temporary:
            return ProviderInputItem.recalled_assistant(message.content, tool_calls)
        return ProviderInputItem.business_assistant(message.content, tool_calls)
    if message.role == "tool":
        if message.tool_call_id is None:
            raise ValueError("tool message requires tool_call_id")
        if ref.temporary:
            return ProviderInputItem.recalled_tool(
                message.tool_call_id,
                message.content,
            )
        return ProviderInputItem.tool_result(message.tool_call_id, message.content)
    raise ValueError("unsupported message role")


def _validate_stored_message_shape(message: StoredMessage) -> None:
    if message.role == "user":
        invalid = bool(message.tool_calls) or message.tool_call_id is not None
    elif message.role == "assistant":
        invalid = message.tool_call_id is not None
    elif message.role == "tool":
        if message.tool_call_id is None:
            raise ValueError("tool message requires tool_call_id")
        invalid = bool(message.tool_calls)
    else:
        raise ValueError("unsupported message role")
    if invalid:
        raise ValueError(f"invalid stored message shape for role: {message.role}")
