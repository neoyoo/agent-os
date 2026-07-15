"""Provider timeout 错误映射。"""

from __future__ import annotations

from agentos.providers.base import ProviderTimeoutError


def raise_for_provider_timeout(
    error: Exception,
    *,
    provider_name: str,
) -> None:
    """把 SDK/transport timeout 映射为稳定领域错误，其余错误原样保留。"""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TimeoutError) or type(current).__name__ == (
            "APITimeoutError"
        ):
            raise ProviderTimeoutError(
                f"{provider_name} request timed out",
            ) from error
        current = current.__cause__ or current.__context__
