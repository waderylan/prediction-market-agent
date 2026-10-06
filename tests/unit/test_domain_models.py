from datetime import UTC, datetime
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
def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timestamp must include a timezone"):
        _market(close_time=datetime(2028, 1, 1, 12))
