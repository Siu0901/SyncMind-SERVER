from app.core.exception.exceptions import AppException


class UserNotFoundError(AppException):
    def __init__(self, user_id: int):
        super().__init__(
            f"User {user_id} not found",
            404,
        )


class PermissionDeniedError(AppException):
    def __init__(self):
        super().__init__(
            "Permission denied",
            403,
        )


class EmailAlreadyExistsError(AppException):
    def __init__(self):
        super().__init__(
            "이미 사용 중인 이메일입니다.",
            409,
        )


class RegisterRequestNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "진행 중인 회원가입 요청이 없습니다.",
            404,
        )

class InvalidVerificationCodeError(AppException):
    def __init__(self):
        super().__init__(
            "인증 코드가 올바르지 않습니다.",
            400,
        )


class ExpiredCodeOrRequestNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "인증 코드가 만료되었거나 회원가입 요청이 존재하지 않습니다.",
            status_code=400,
        )


class VerificationAttemptsExceededError(AppException):
    def __init__(self):
        super().__init__(
            "인증 시도 횟수를 초과했습니다.",
            429,
        )


class VerificationResendCooldownError(AppException):
    def __init__(self):
        super().__init__(
            "잠시 후 다시 요청해주세요.",
            429,
        )


class InvalidCredentialsError(AppException):
    def __init__(self):
        super().__init__(
            "이메일 또는 비밀번호가 올바르지 않습니다.",
            401,
        )

class InactiveUserError(AppException):
    def __init__(self, message: str = "비활성화된 계정입니다"):
        super().__init__(
            message,
            403,
        )


class TokenExpiredError(AppException):
    def __init__(self):
        super().__init__(
            "Token expired",
            401,
        )


class TokenInvalidError(AppException):
    def __init__(self):
        super().__init__(
            "Token invalid",
            401,
        )


class SessionExpiredError(AppException):
    def __init__(self):
        super().__init__(
            "Session expired or revoked",
            401,
        )


class InvalidOAuthStateError(AppException):
    def __init__(
        self,
        message: str = "Invalid or expired OAuth state",
    ):
        super().__init__(
            message,
            401,
        )


class OAuthEmailConflictError(AppException):
    def __init__(self):
        super().__init__(
            "이미 다른 로그인 방식으로 가입된 이메일입니다.",
            409,
        )


class OAuthProviderError(AppException):
    def __init__(
        self,
        provider: str,
    ):
        super().__init__(
            f"{provider} OAuth 처리 중 오류가 발생했습니다.",
            502,
        )


class OAuthEmailNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "OAuth 계정에서 이메일을 가져올 수 없습니다.",
            400,
        )


class OAuthTicketInvalidError(AppException):
    def __init__(self):
        super().__init__(
            "OAuth login ticket이 만료되었거나 유효하지 않습니다.",
            401,
        )