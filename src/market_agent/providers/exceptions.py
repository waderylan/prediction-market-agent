"""Typed failures exposed by provider clients."""

from typing import Any


class MarketDataError(RuntimeError):
    """Base class for inspectable provider failures."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        self.message = message
        super().__init__(f"{provider}: {message}")


class MarketTransportError(MarketDataError):
    """Network connection or timeout failure after bounded retries."""

    def __init__(self, provider: str, message: str, *, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(provider, message)


class MarketHTTPError(MarketDataError):
    """Non-success HTTP response."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int,
        retryable: bool,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(provider, message)


class MarketValidationError(MarketDataError):
    """Malformed or semantically invalid provider response."""


class MarketMissingDataError(MarketDataError):
    """Required identity data is absent from a provider response."""


class MarketRequestError(ValueError):
    """Safe, stable diagnostic for a caller-correctable market request."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        fields: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.fields = fields or {}
        super().__init__(message)
