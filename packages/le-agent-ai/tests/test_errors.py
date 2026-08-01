from le_agent_ai.errors import normalize_error_code


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: object = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def test_provider_errors_are_normalized_without_provider_specific_types() -> None:
    assert (
        normalize_error_code(
            ProviderError(
                "This model's maximum context length was exceeded",
                400,
                {"error": {"code": "context_length_exceeded"}},
            )
        )
        == "context_overflow"
    )
    assert normalize_error_code(ProviderError("too many requests", 429)) == "rate_limit"
    assert normalize_error_code(ProviderError("bad key", 401)) == "authentication"
    assert normalize_error_code(TimeoutError("timed out")) == "timeout"
    assert normalize_error_code(ProviderError("upstream", 503)) == "server_error"
    assert normalize_error_code(OSError("connection reset")) == "network"
