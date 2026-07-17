class SkillActivationStoreClosedError(RuntimeError):
    """Skill Activation Store 已关闭。"""


class SkillActivationCorruptedError(RuntimeError):
    """持久化 Skill Activation 无法通过严格校验。"""


__all__ = [
    "SkillActivationCorruptedError",
    "SkillActivationStoreClosedError",
]
