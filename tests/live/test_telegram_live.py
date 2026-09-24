"""Opt-in Telegram smoke; excluded cleanly when deployment-owned credentials are absent."""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from market_agent.watch.delivery import TelegramDelivery
from market_agent.watch.models import EvidenceDelta, GameIdentity, WatchTrigger

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
