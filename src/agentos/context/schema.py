from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WorkingStateField:
    """LLM 可见 working state schema 中的字段声明。"""

    name: str
    type: str
    purpose: str


@dataclass(frozen=True, slots=True)
class WorkingStateSchema:
    """当前 chapter 内锁定的 working state schema。"""

    fields: tuple[WorkingStateField, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """把字段序列冻结为 tuple，避免外部绕过 schema 锁定。"""

        object.__setattr__(self, "fields", tuple(self.fields))
