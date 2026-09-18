import os

import pytest

from market_agent.providers import KalshiClient, PolymarketClient

pytestmark = pytest.mark.live_smoke


def _live_enabled() -> bool:
    return os.getenv("RUN_LIVE_SMOKE") == "1"


@pytest.mark.skipif(not _live_enabled(), reason="set RUN_LIVE_SMOKE=1 to call public APIs")
async def test_polymarket_public_detail_live() -> None:
    async with PolymarketClient(timeout_seconds=10, max_retries=1) as client:
        market = await client.get_market("561229")

    assert market.market_id == "561229"
    assert market.title


@pytest.mark.skipif(not _live_enabled(), reason="set RUN_LIVE_SMOKE=1 to call public APIs")
async def test_kalshi_public_detail_live() -> None:
    async with KalshiClient(timeout_seconds=10, max_retries=1) as client:
        market = await client.get_market("KXPRESPERSON-28-JVAN")

    assert market.market_id == "KXPRESPERSON-28-JVAN"
    assert market.title
