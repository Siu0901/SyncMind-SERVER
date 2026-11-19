from app.core.exception.exceptions import AppException


class ChatAgentError(AppException):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
        )


class ChatAgentTimeoutError(ChatAgentError):
    def __init__(self):
        super().__init__(
            message="Chat agent execution timed out",
            status_code=504,
        )