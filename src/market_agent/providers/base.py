"""Shared HTTP mechanics; provider response parsing stays in provider modules."""

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Self

import httpx

from market_agent.providers.exceptions import (
    MarketHTTPError,
    MarketTransportError,
    MarketValidationError,
)


class AsyncMarketClient:
    """Bounded asynchronous GET client for public market-data APIs."""

    provider: str

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": "cross-market-agent/0.1"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _request_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> Any:
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http.get(path, params=params, timeout=self.timeout)
            except httpx.TransportError as error:
                if attempt < attempts:
                    await self._backoff(attempt)
                    continue
                raise MarketTransportError(
                    self.provider,
                    f"request failed after {attempt} attempt(s): {type(error).__name__}",
                    attempts=attempt,
                ) from error

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < attempts:
                await self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if not response.is_success:
                body_preview = " ".join(response.text.split())[:200]
                raise MarketHTTPError(
                    self.provider,
                    f"HTTP {response.status_code}: {body_preview or 'empty response'}",
                    status_code=response.status_code,
                    retryable=retryable,
                )
            try:
                return response.json()
            except ValueError as error:
                raise MarketValidationError(
                    self.provider, "response body is not valid JSON"
                ) from error
        raise AssertionError("retry loop exhausted unexpectedly")

    async def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
        if retry_after is not None:
            with suppress(ValueError):
                delay = min(max(float(retry_after), 0.0), 2.0)
        if delay:
            await asyncio.sleep(delay)
