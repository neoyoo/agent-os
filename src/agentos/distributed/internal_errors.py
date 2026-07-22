class StaleInternalSubmissionAuthorityError(RuntimeError):
    """TeamDelivery claim 已失效，禁止创建 internal Run。"""

    code = "stale_internal_submission_authority"

    def __init__(self) -> None:
        super().__init__("internal submission authority is stale")


class InternalSubmissionBindingRevokedError(RuntimeError):
    """内部提交线性化前，目标 Team binding 已撤销。"""

    code = "internal_submission_binding_revoked"

    def __init__(self) -> None:
        super().__init__("internal submission binding was revoked")


__all__ = [
    "InternalSubmissionBindingRevokedError",
    "StaleInternalSubmissionAuthorityError",
]
