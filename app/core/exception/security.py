from app.core.exception.exceptions import AppException


class TokenExpiredError(AppException):
    def __init__(self):
        super().__init__("Token expired", 401)


class TokenInvalidError(AppException):
    def __init__(self):
        super().__init__("Token invalid", 401)
