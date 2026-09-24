"""Run the shared SQLite watch coordinator and Telegram outbox in the foreground."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from market_agent.watch.coordinator import MCPWatchEvidenceProvider, WatchCoordinator
from market_agent.watch.delivery import (
    DeliveryWorker,
    TelegramDelivery,
    TransportResponse,
)
from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    GameIdentity,
    MarketIdentity,
    PriceMoveCondition,
    WatchObservation,
    WatchRule,
)
from market_agent.watch.repository import SQLiteWatchRepository


async def run(database: str, once: bool) -> None:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    repository = SQLiteWatchRepository(path)
    coordinator = WatchCoordinator(repository, MCPWatchEvidenceProvider())
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    telegram = TelegramDelivery(SecretStr(token), SecretStr(chat_id)) if token and chat_id else None
    delivery = DeliveryWorker(repository, telegram)
    owner = f"foreground-{uuid4().hex}"
    while True:
        result = await coordinator.poll(owner=owner)
        attempts = await delivery.run_once(owner + "-delivery")
        print(
            f"claimed={result.claimed_watches} groups={result.observation_groups} "
            f"triggers={result.created_triggers} warnings={result.source_warnings} "
            f"deliveries={attempts}",
            flush=True,
        )
        if once:
            return
        await asyncio.sleep(60)


class ReplayEvidence:
    def __init__(self, observations: list[WatchObservation]) -> None:
        self.observations = observations
        self.index = 0

    async def observe(self, _: WatchRule, __: datetime) -> WatchObservation:
        observation = self.observations[min(self.index, len(self.observations) - 1)]
        self.index += 1
        return observation


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, _: SecretStr, __: SecretStr, text: str) -> TransportResponse:
        self.messages.append(text)
        return TransportResponse(200, {"ok": True, "result": {"message_id": len(self.messages)}})


async def replay_fixture(fixture: str) -> None:
    payload: dict[str, Any] = json.loads(Path(fixture).read_text(encoding="utf-8"))
    game = GameIdentity.model_validate_json(json.dumps(payload["game"]))
    markets = [MarketIdentity.model_validate_json(json.dumps(item)) for item in payload["markets"]]
    observations = [
        WatchObservation.model_validate_json(json.dumps(item)) for item in payload["timeline"]
    ]
    rule = WatchRule(
        watch_id="watch_cccccccccccccccccccccccccccccccc",
        session_id="historical-replay",
        confirmed_at=observations[0].retrieved_at,
        creation_source="cli",
        game=game,
        markets=markets,
        conditions=[
            PriceMoveCondition(
                condition_id="scoring_move",
                threshold=Decimal("0.08"),
                window_seconds=120,
                event_relationship="scoring_event",
                correlation_window_seconds=120,
                cooldown_seconds=300,
                rearm_below=Decimal("0.02"),
            ),
            PriceMoveCondition(
                condition_id="untracked_move",
                threshold=Decimal("0.12"),
                window_seconds=120,
                event_relationship="no_tracked_scoring_event",
                correlation_window_seconds=120,
                cooldown_seconds=300,
                rearm_below=Decimal("0.02"),
            ),
        ],
        delivery=DeliveryPolicy(
            channels=frozenset({DeliveryChannel.INBOX, DeliveryChannel.TELEGRAM})
        ),
    )
    with tempfile.TemporaryDirectory(prefix="watch-replay-") as directory:
        repository = SQLiteWatchRepository(Path(directory) / "replay.db")
        repository.save_rule(rule, due_at=observations[0].retrieved_at)
        coordinator = WatchCoordinator(repository, ReplayEvidence(observations))
        started = time.perf_counter()
        first = await coordinator.poll(owner="replay", now=observations[0].retrieved_at)
        second = await coordinator.poll(owner="replay", now=observations[1].retrieved_at)
        duplicate = await coordinator.poll(
            owner="replay", now=observations[1].retrieved_at + timedelta(minutes=1)
        )
        poll_elapsed_ms = round((time.perf_counter() - started) * 1000)
        transport = FakeTelegramTransport()
        telegram = TelegramDelivery(SecretStr("fake-token"), SecretStr("fake-chat"), transport)
        delivered = await DeliveryWorker(repository, telegram).run_once(
            "replay-delivery", observations[1].retrieved_at
        )
        alerts = repository.list_triggers("historical-replay")
        stored_observations = repository.recent_observations(
            rule.watch_id, observations[0].retrieved_at - timedelta(seconds=1)
        )
        quote_to_trigger_seconds = (
            max(
                round((alerts[0].triggered_at - quote.quote_time).total_seconds())
                for quote in observations[1].quotes
                if quote.quote_time is not None
            )
            if alerts
            else -1
        )
        print(
            f"timeline={len(observations)} baseline_triggers={first.created_triggers} "
            f"boundary_triggers={second.created_triggers} inbox={len(alerts)} "
            f"fake_telegram={delivered} model_calls={second.model_calls} "
            f"tavily_calls={second.tavily_calls} duplicate_poll_triggers="
            f"{duplicate.created_triggers} stored_observations={len(stored_observations)} "
            f"fixture_quote_to_trigger_seconds={quote_to_trigger_seconds} "
            f"poll_elapsed_ms={poll_elapsed_ms}",
            flush=True,
        )
        if alerts:
            safe_message = (
                alerts[0]
                .message.encode(sys.stdout.encoding or "utf-8", errors="replace")
                .decode(sys.stdout.encoding or "utf-8")
            )
            print(safe_message, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="artifacts/watches.db")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--replay", help="Run a deterministic fixture without external services")
    args = parser.parse_args()
    try:
        if args.replay:
            asyncio.run(replay_fixture(args.replay))
        else:
            asyncio.run(run(args.db, args.once))
    except KeyboardInterrupt:
        print("watch runner stopped; saved SQLite watches remain active for the next run")


if __name__ == "__main__":
    main()
