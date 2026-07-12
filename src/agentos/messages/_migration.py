"""Phase 2 消息类型迁移桥。"""

from agentos.messages.types import Message, StoredMessage

# Phase 2 migration bridge; remove in Task 13.
__all__ = ["Message", "StoredMessage"]
