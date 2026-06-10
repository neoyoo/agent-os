from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """工具执行资源限制声明。"""

    deadline_seconds: float | None = None
    memory_limit_mb: int | None = None
    network_allowlist: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """校验资源限制参数。"""

        if self.deadline_seconds is not None and self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if self.memory_limit_mb is not None and self.memory_limit_mb < 1:
            raise ValueError("memory_limit_mb must be at least 1")
