from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from market_agent.domain import CanonicalMarket, MarketStatus, Platform


def _market(**changes: object) -> CanonicalMarket:
    values: dict[str, object] = {
        "platform": Platform.POLYMARKET,
        "market_id": "market-1",
        "title": "Example market",
        "outcomes": ("Yes", "No"),
        "yes_price": Decimal("0.4"),
        "no_price": Decimal("0.6"),
        "status": MarketStatus.OPEN,
        "source_url": "https://example.test/market-1",
        "retrieved_at": datetime.now(UTC),
    }
    values.update(changes)
    return CanonicalMarket.model_validate(values)


@pytest.mark.unit
def test_probability_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError, match="probability must be between 0 and 1"):
        _market(yes_price=Decimal("1.01"))


@pytest.mark.unit
def test_timestamps_are_normalized_to_utc() -> None:
    source = datetime(2028, 1, 1, 12, tzinfo=timezone(timedelta(hours=-5)))

    market = _market(close_time=source)

    assert market.close_time == datetime(2028, 1, 1, 17, tzinfo=UTC)


@pytest.mark.unit
def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timestamp must include a timezone"):
        _market(close_time=datetime(2028, 1, 1, 12))
