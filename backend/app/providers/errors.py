class OddsProviderError(Exception):
    """Base error for failures while fetching or normalizing provider data."""

    error_type = "provider_error"


class ProviderTimeoutError(OddsProviderError):
    error_type = "timeout"


class ProviderAuthError(OddsProviderError):
    error_type = "auth_failed"


class ProviderRateLimitError(OddsProviderError):
    error_type = "rate_limited"


class ProviderQuotaError(OddsProviderError):
    error_type = "quota_low"


class ProviderUpstreamError(OddsProviderError):
    error_type = "upstream_error"


class ProviderPayloadError(OddsProviderError):
    error_type = "invalid_payload"


class ProviderIncompleteResponseError(ProviderPayloadError):
    """The provider returned a valid-looking but provably incomplete data set."""

    error_type = "incomplete_response"


class ProviderRequestRejectedError(ProviderUpstreamError):
    """The provider answered with a 4xx other than auth/rate-limit (bad request)."""
