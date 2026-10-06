"""Production MCP session lifetime and partial recovery over real stdio."""

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from market_agent.mcp.pool import MCPToolPool

pytestmark = pytest.mark.integration


def _working_server() -> dict:
    script = Path(__file__).parents[1] / "support" / "polymarket_server.py"
    return {"transport": "stdio", "command": sys.executable, "args": [str(script)]}


async def test_reuses_session_and_recovers_one_failed_server():
    pool = MCPToolPool(
        {
            "polymarket": _working_server(),
            "sports_state": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "market_agent.mcp.sports_state"],
            },
        }
    )
    await pool.start()
    try:
        async with pool.tools() as tools:
            first = next(tool for tool in tools if tool.name == "polymarket_get_market")
            concurrent = await asyncio.gather(
                *(
                    first.ainvoke(
                        {
                            "type": "tool_call",
                            "name": "polymarket_get_market",
                            "args": {"market_id": "561229"},
                            "id": f"shared-{index}",
                        }
                    )
                    for index in range(2)
                )
            )
            assert all(
                isinstance(result, ToolMessage) and result.status == "success"
                for result in concurrent
            )
            pool.transport_failed("polymarket")
            assert pool.available_sources() == ["sports_state"]
            assert first in tools  # Active turns retain their tool snapshot.

        async with pool.tools() as tools:
            assert {tool.name for tool in tools} == {
                "sports_state_find_games",
                "sports_state_get_game_state",
                "sports_state_get_box_score",
                "sports_state_list_players",
                "sports_state_get_player_stats",
                "sports_state_get_play_by_play",
            }

        async with asyncio.timeout(8):
            while "polymarket" not in pool.available_sources():
                await asyncio.sleep(0.05)

        async with pool.tools() as tools:
            second = next(tool for tool in tools if tool.name == "polymarket_get_market")
            assert second is not first
            result = await second.ainvoke(
                {
                    "type": "tool_call",
                    "name": "polymarket_get_market",
                    "args": {"market_id": "561229"},
                    "id": "pool-call",
                }
            )
            assert isinstance(result, ToolMessage)
            assert result.status == "success"
            assert result.artifact["structured_content"]["market_id"] == "561229"
    finally:
        await pool.close()
