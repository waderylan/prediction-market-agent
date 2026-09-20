"""Shared HTTP mechanics; provider response parsing stays in provider modules."""

import asyncio
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from uuid import uuid4

import httpx

from market_agent.domain import CanonicalMarket
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
        max_response_bytes: int = 10_000_000,
        observation_cache_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if observation_cache_seconds <= 0:
            raise ValueError("observation_cache_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.max_response_bytes = max_response_bytes
        self.observation_cache_ttl = timedelta(seconds=observation_cache_seconds)
        self._observations: OrderedDict[str, tuple[datetime, CanonicalMarket]] = OrderedDict()
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
            if len(response.content) > self.max_response_bytes:
                raise MarketValidationError(
                    self.provider,
                    f"response exceeds {self.max_response_bytes} byte safety limit",
                )
            try:
                return response.json()
            except ValueError as error:
                raise MarketValidationError(
                    self.provider, "response body is not valid JSON"
                ) from error
        raise AssertionError("retry loop exhausted unexpectedly")

    def record_observation(self, market: CanonicalMarket) -> CanonicalMarket:
        """Assign an identity to one normalized provider response and cache it briefly."""
        observed = market.model_copy(
            update={
                "provider_data": {
                    **market.provider_data,
                    "observation_id": f"{self.provider}:{uuid4()}",
                    "cache_hit": False,
                    "cache_age_ms": 0,
                }
            }
        )
        self._observations[market.market_id] = (datetime.now(UTC), observed)
        self._observations.move_to_end(market.market_id)
        while len(self._observations) > 256:
            self._observations.popitem(last=False)
        return observed

    def reuse_observation(self, detail: CanonicalMarket) -> CanonicalMarket:
        """Keep immediate search/detail quote fields tied to one observable snapshot."""
        cached = self._observations.get(detail.market_id)
        if cached is None:
            return self.record_observation(detail)
        cached_at, observation = cached
        age = datetime.now(UTC) - cached_at
        if age > self.observation_cache_ttl:
            del self._observations[detail.market_id]
            return self.record_observation(detail)
        self._observations.move_to_end(detail.market_id)
        quote_keys = {
            "outcome_quotes",
            "provider_updated_at",
            "provider_last_trade_price",
            "provider_last_trade_outcome",
            "price_observed_at",
            "last_trade_at",
            "last_price",
            "last_trade_price",
        }
        context_keys = {"sports", "event_slug", "series_ticker"}
        provider_data = dict(detail.provider_data)
        for key in quote_keys | context_keys:
            if key in observation.provider_data:
                provider_data[key] = observation.provider_data[key]
        provider_data.update(
            observation_id=observation.provider_data["observation_id"],
            cache_hit=True,
            cache_age_ms=max(0, int(age.total_seconds() * 1000)),
        )
        return detail.model_copy(
            update={
                "event_id": observation.event_id or detail.event_id,
                "yes_price": observation.yes_price,
                "no_price": observation.no_price,
                "yes_bid": observation.yes_bid,
                "yes_ask": observation.yes_ask,
                "retrieved_at": observation.retrieved_at,
                "provider_data": provider_data,
            }
        )

    async def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
        if retry_after is not None:
            with suppress(ValueError):
                delay = min(max(float(retry_after), 0.0), 2.0)
        if delay:
            await asyncio.sleep(delay)
