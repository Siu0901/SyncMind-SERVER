from app.core.exception.exceptions import AppException


class WorkspaceNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace not found",
            404,
        )


class WorkspacePermissionDeniedError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace permission denied",
            403,
        )


class WorkspaceMemberNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace member not found",
            404,
        )


class WorkspaceMemberAlreadyExistsError(AppException):
    def __init__(self):
        super().__init__(
            "User is already a workspace member",
            409,
        )


class WorkspaceOwnerRequiredError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace owner permission required",
            403,
        )


class CannotRemoveWorkspaceOwnerError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace owner cannot be removed",
            400,
        )


class CannotChangeWorkspaceOwnerRoleError(AppException):
    def __init__(self):
        super().__init__(
            "Workspace owner role cannot be changed",
            400,
        )