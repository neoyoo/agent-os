class HttpTransportError(ValueError):
    """HTTP wire 边界的固定 code/message 错误。"""

    status_code = 400
    code = "invalid_request"
    message = "invalid request"

    def __init__(self) -> None:
        super().__init__(self.message)


class HttpParseError(HttpTransportError):
    """JSON bytes 不满足严格语法。"""

    code = "invalid_json"
    message = "invalid JSON request"


class HttpValidationError(HttpTransportError):
    """HTTP 请求字段不满足 wire contract。"""


class AuthenticationRequiredError(HttpTransportError):
    """Channel 未获得已验证 identity。"""

    status_code = 401
    code = "authentication_required"
    message = "authentication required"


class PermissionDeniedError(HttpTransportError):
    """已验证 identity 无权执行当前 operation。"""

    status_code = 403
    code = "permission_denied"
    message = "permission denied"


class RequestTooLargeError(HttpTransportError):
    """完整 HTTP request body 超过 wire hard limit。"""

    status_code = 413
    code = "request_too_large"
    message = "request exceeds maximum size"


class UnsupportedMediaTypeError(HttpTransportError):
    """HTTP Content-Type 不受当前 operation 支持。"""

    status_code = 415
    code = "unsupported_media_type"
    message = "unsupported media type"


__all__ = [
    "AuthenticationRequiredError",
    "HttpParseError",
    "HttpTransportError",
    "HttpValidationError",
    "PermissionDeniedError",
    "RequestTooLargeError",
    "UnsupportedMediaTypeError",
]
