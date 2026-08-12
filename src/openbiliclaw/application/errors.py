"""Safe closed application workflow failures."""

from enum import StrEnum


class ApplicationErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"


class ApplicationError(Exception):
    def __init__(self, code: ApplicationErrorCode, message: str) -> None:
        self.code = code
        self.safe_message = message
        super().__init__(f"{code.value}: {message}")
