from app.core.exception.exceptions import AppException


class UserNotFoundError(AppException):
    def __init__(self, user_id: int):
        super().__init__(
            f"User {user_id} not found",
            404
        )
