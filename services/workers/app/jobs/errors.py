"""Error taxonomy (docs/01): every failure is classified; the class decides retry behaviour."""

from enum import StrEnum


class ErrorClass(StrEnum):
    TRANSIENT = "transient"  # retry with backoff
    RATE_LIMITED = "rate_limited"  # retry after Retry-After
    ACCESS_RESTRICTED = "access_restricted"  # stop, never retry (compliance)
    PARSE_FAILED = "parse_failed"  # flag the connector, no retry
    INVALID_INPUT = "invalid_input"  # fail fast
    BUDGET_EXHAUSTED = "budget_exhausted"  # pause the job, ask the user


RETRYABLE = frozenset({ErrorClass.TRANSIENT, ErrorClass.RATE_LIMITED})


class JobError(Exception):
    """Base for classified job failures. Unknown exceptions are treated as TRANSIENT."""

    error_class: ErrorClass = ErrorClass.TRANSIENT

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class TransientError(JobError):
    error_class = ErrorClass.TRANSIENT


class RateLimitedError(JobError):
    error_class = ErrorClass.RATE_LIMITED


class AccessRestrictedError(JobError):
    error_class = ErrorClass.ACCESS_RESTRICTED


class ParseFailedError(JobError):
    error_class = ErrorClass.PARSE_FAILED


class InvalidInputError(JobError):
    error_class = ErrorClass.INVALID_INPUT


class BudgetExhaustedError(JobError):
    error_class = ErrorClass.BUDGET_EXHAUSTED


def classify(exc: BaseException) -> ErrorClass:
    if isinstance(exc, JobError):
        return exc.error_class
    return ErrorClass.TRANSIENT
