from fastapi import status


class PypeError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "PYPE_ERROR"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuthError(PypeError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"


class RoleMismatchError(PypeError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "ROLE_MISMATCH"


class ForbiddenError(PypeError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class TimeoutOutOfRangeError(PypeError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "TIMEOUT_OUT_OF_RANGE"
