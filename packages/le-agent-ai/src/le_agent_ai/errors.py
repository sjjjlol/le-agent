"""Provider-independent error classification used by recovery policy."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

ErrorCode: TypeAlias = Literal[
    "context_overflow",
    "rate_limit",
    "timeout",
    "authentication",
    "permission",
    "server_error",
    "invalid_request",
    "network",
    "provider_error",
    "aborted",
]


def normalize_error_code(error: BaseException) -> ErrorCode:
    status = getattr(error, "status_code", None)
    body: Any = getattr(error, "body", None)
    evidence = f"{error} {body!r}".casefold()
    overflow_markers = (
        "context_length_exceeded",
        "context window",
        "maximum context length",
        "prompt is too long",
        "input is too long",
        "too many tokens",
        "request too large",
    )
    if any(marker in evidence for marker in overflow_markers):
        return "context_overflow"
    if isinstance(error, TimeoutError) or "timed out" in evidence or "timeout" in evidence:
        return "timeout"
    if status == 429:
        return "rate_limit"
    if status == 401:
        return "authentication"
    if status == 403:
        return "permission"
    if isinstance(status, int) and status >= 500:
        return "server_error"
    if isinstance(status, int) and 400 <= status < 500:
        return "invalid_request"
    if isinstance(error, OSError):
        return "network"
    return "provider_error"
