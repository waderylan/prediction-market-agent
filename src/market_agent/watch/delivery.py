"""Telegram projection of stored triggers with bounded retries and sanitized failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from market_agent.watch.models import OutboxItem, OutboxStatus, WatchTrigger
from market_agent.watch.repository import WatchRepository


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    payload: dict[str, Any] | None


class TelegramTransport(Protocol):
    async def send(self, token: SecretStr, chat_id: SecretStr, text: str) -> TransportResponse: ...


class HttpxTelegramTransport:
    """Small HTTPS boundary; token and recipient never enter an exception or log message."""

    async def send(self, token: SecretStr, chat_id: SecretStr, text: str) -> TransportResponse:
        timeout = httpx.Timeout(connect=5, read=10, write=10, pool=5)
        url = f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    json={"chat_id": chat_id.get_secret_value(), "text": text},
                )
        except httpx.TransportError as error:
            raise TelegramTransientError(type(error).__name__) from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return TransportResponse(
            response.status_code, payload if isinstance(payload, dict) else None
        )


class TelegramTransientError(RuntimeError):
    def __init__(self, classification: str, retry_after: int | None = None) -> None:
        super().__init__(classification)
        self.classification = classification
        self.retry_after = retry_after


class TelegramTerminalError(RuntimeError):
    def __init__(self, classification: str) -> None:
        super().__init__(classification)
        self.classification = classification


class TelegramDelivery:
    def __init__(
        self,
        token: SecretStr,
        chat_id: SecretStr,
        transport: TelegramTransport | None = None,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._transport = transport or HttpxTelegramTransport()

    async def deliver(self, trigger: WatchTrigger) -> str:
        if len(trigger.message) > 1500:
            raise TelegramTerminalError("message_too_long")
        response = await self._transport.send(self._token, self._chat_id, trigger.message)
        payload = response.payload
        if response.status_code == 429:
            retry = None
            if payload:
                parameters = payload.get("parameters")
                if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int):
                    retry = max(1, min(parameters["retry_after"], 3600))
            raise TelegramTransientError("rate_limited", retry)
        if response.status_code >= 500:
            raise TelegramTransientError("server_error")
        if response.status_code in {400, 401, 403, 404}:
            description = str(payload.get("description", "")) if payload else ""
            classification = (
                "recipient_blocked"
                if "blocked" in description.casefold()
                else "authorization_or_client_error"
            )
            raise TelegramTerminalError(classification)
        if response.status_code < 200 or response.status_code >= 300:
            raise TelegramTerminalError("client_error")
        if not payload or payload.get("ok") is not True:
            raise TelegramTransientError("malformed_response")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramTransientError("malformed_response")
        return str(result["message_id"])


class DeliveryWorker:
    def __init__(
        self,
        repository: WatchRepository,
        telegram: TelegramDelivery | None,
        *,
        max_attempts: int = 5,
    ) -> None:
        self.repository = repository
        self.telegram = telegram
        self.max_attempts = max_attempts

    @staticmethod
    def _backoff(attempt: int) -> int:
        return min(300, 1 << min(attempt, 8))

    async def run_once(self, owner: str, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        items: list[OutboxItem] = self.repository.claim_outbox(owner, timestamp)
        for item in items:
            await self._deliver_item(item, owner, timestamp)
        return len(items)

    async def _deliver_item(self, item: OutboxItem, owner: str, now: datetime) -> None:
        trigger = self.repository.trigger_by_id(item.trigger_id)
        if trigger is None:
            self.repository.finish_outbox(
                item.outbox_id,
                owner,
                OutboxStatus.FAILED,
                available_at=now,
                error_class="missing_trigger",
            )
            return
        if self.telegram is None:
            self.repository.finish_outbox(
                item.outbox_id,
                owner,
                OutboxStatus.RETRY,
                available_at=now + timedelta(minutes=15),
                error_class="telegram_not_configured",
            )
            return
        try:
            message_id = await self.telegram.deliver(trigger)
        except TelegramTerminalError as error:
            self.repository.finish_outbox(
                item.outbox_id,
                owner,
                OutboxStatus.FAILED,
                available_at=now,
                error_class=error.classification,
            )
        except TelegramTransientError as error:
            terminal = item.attempts >= self.max_attempts
            delay = error.retry_after or self._backoff(item.attempts)
            self.repository.finish_outbox(
                item.outbox_id,
                owner,
                OutboxStatus.FAILED if terminal else OutboxStatus.RETRY,
                available_at=now if terminal else now + timedelta(seconds=delay),
                error_class=("retry_exhausted" if terminal else error.classification),
            )
        else:
            self.repository.finish_outbox(
                item.outbox_id,
                owner,
                OutboxStatus.SENT,
                available_at=now,
                provider_message_id=message_id,
            )
