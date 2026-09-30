"""Opt-in Telegram smoke; excluded cleanly when deployment-owned credentials are absent."""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from market_agent.watch.coordinator import WatchCoordinator
from market_agent.watch.delivery import DeliveryWorker, TelegramDelivery
from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    EvidenceDelta,
    GameIdentity,
    MarketIdentity,
    PriceMoveCondition,
    WatchLifecycleEvent,
    WatchObservation,
    WatchRule,
    WatchTrigger,
)
from market_agent.watch.repository import SQLiteWatchRepository

pytestmark = [
    pytest.mark.live_smoke,
    pytest.mark.skipif(
        not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        reason="set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
    ),
]


async def test_telegram_delivery_live() -> None:
    trigger = WatchTrigger(
        trigger_id="trigger_ffffffffffffffffffffffffffffffff",
        watch_id="watch_ffffffffffffffffffffffffffffffff",
        session_id="live-smoke",
        condition_id="live_smoke",
        fingerprint="f" * 64,
        triggered_at=datetime.now(UTC),
        game=GameIdentity(
            league="mlb",
            game_ref="live-smoke-not-a-provider-reference",
            home_team="Telegram",
            away_team="Watch Engine",
            scheduled_start=datetime.now(UTC),
        ),
        deltas=[
            EvidenceDelta(
                platform="smoke",
                market_id="local-smoke",
                outcome="delivery",
                before_price=Decimal("0.42"),
                after_price=Decimal("0.50"),
                window_seconds=60,
            )
        ],
        observation_ids=["live-smoke"],
        message=(
            "WATCH ALERT — opt-in live Telegram smoke\n"
            "No provider event is represented. Timing alignment is correlation, "
            "not proof of causation."
        ),
    )
    delivery = TelegramDelivery(
        SecretStr(os.environ["TELEGRAM_BOT_TOKEN"]),
        SecretStr(os.environ["TELEGRAM_CHAT_ID"]),
    )
    assert int(await delivery.deliver(trigger)) > 0


async def test_local_first_poll_delivers_real_activation(tmp_path: Path) -> None:
    """Opt-in end-to-end activation using recorded MCP evidence and real Telegram."""
    payload = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "watch" / "yankees_scoring_replay.json"
        ).read_text(encoding="utf-8")
    )
    first = WatchObservation.model_validate_json(json.dumps(payload["timeline"][0]))
    watched = WatchRule(
        watch_id=f"watch_{uuid4().hex}",
        session_id="live-activation-smoke",
        confirmed_at=first.retrieved_at,
        creation_source="cli",
        game=GameIdentity.model_validate_json(json.dumps(payload["game"])),
        markets=[MarketIdentity.model_validate_json(json.dumps(m)) for m in payload["markets"]],
        conditions=[
            PriceMoveCondition(
                condition_id="move",
                threshold=Decimal("0.08"),
                window_seconds=120,
                event_relationship="any",
            )
        ],
        delivery=DeliveryPolicy(
            channels=frozenset(
                {
                    DeliveryChannel.INBOX,
                    DeliveryChannel.TELEGRAM,
                }
            )
        ),
    )
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    repository.save_rule(watched, due_at=first.retrieved_at)
    assert repository.list_events(watched.session_id) == []

    class Evidence:
        async def observe(self, rule: WatchRule, now: datetime) -> WatchObservation:
            return first

    result = await WatchCoordinator(repository, Evidence()).poll(
        owner="foreground-live-smoke",
        now=first.retrieved_at,
    )
    assert result.lifecycle_events == 1
    event = repository.list_events(watched.session_id)[0]
    assert isinstance(event, WatchLifecycleEvent)
    assert event.kind.value == "monitoring_started"
    delivery = TelegramDelivery(
        SecretStr(os.environ["TELEGRAM_BOT_TOKEN"]),
        SecretStr(os.environ["TELEGRAM_CHAT_ID"]),
    )
    assert (
        await DeliveryWorker(repository, delivery).run_once(
            "live-activation-delivery",
            first.retrieved_at,
        )
        == 1
    )
    assert repository.outbox_for_trigger(event.event_id).status.value == "sent"
