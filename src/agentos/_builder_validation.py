from __future__ import annotations


def require_unset(current: object | None, method_name: str) -> None:
    """拒绝对同一个 Builder 配置入口重复赋值。"""

    if current is not None:
        raise ValueError(
            f"AgentBuilder.{method_name}() called twice. Remove one call.",
        )
