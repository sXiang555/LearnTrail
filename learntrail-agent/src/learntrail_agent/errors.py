"""Public errors contain fixed messages, never upstream response bodies."""


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def payload(self) -> dict[str, object]:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}


class LLMError(AppError):
    """Model errors retain their existing public contract."""


def upstream_error(status: int) -> LLMError:
    if status in {401, 403}:
        return LLMError("LLM_AUTH_ERROR", "模型服务认证失败，请检查本机模型配置。", 502)
    if status == 429:
        return LLMError("LLM_RATE_LIMITED", "模型服务限流，请稍后重试。", 429, True)
    if status >= 500:
        return LLMError("LLM_UNAVAILABLE", "模型服务暂不可用。", 503, True)
    return LLMError("LLM_REQUEST_REJECTED", "模型服务拒绝请求，请检查模型及参数配置。", 502)


def protocol_error() -> LLMError:
    return LLMError("LLM_INVALID_RESPONSE", "模型服务返回了无法解析的响应。", 502)


def timeout_error() -> LLMError:
    return LLMError("LLM_TIMEOUT", "模型请求超时，请稍后重试。", 504, True)
