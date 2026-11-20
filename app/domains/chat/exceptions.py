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


class ConversationNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Conversation not found",
            404,
        )


class ChatMessageNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Chat message not found",
            404,
        )


class ConversationChangedError(AppException):
    def __init__(self):
        super().__init__(
            "Conversation changed while generating the answer. "
            "Reload the conversation and try again.",
            409,
        )


class ChatCitationError(AppException):
    def __init__(self):
        super().__init__(
            "The answer contains invalid or unavailable citations",
            502,
        )