"""Production MCP session lifetime and partial recovery over real stdio."""

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from mcp import McpError

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


async def test_unavailable_server_does_not_hide_healthy_tools():
    pool = MCPToolPool(
        {
            "polymarket": _working_server(),
            "kalshi": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "missing_mcp_pool_test_module"],
            },
        }
    )
    await pool.start()
    try:
        assert pool.available_sources() == ["polymarket"]
        async with pool.tools() as tools:
            assert {tool.name for tool in tools} == {
                "polymarket_search_markets",
                "polymarket_get_market",
            }
    finally:
        await pool.close()


async def test_closed_transport_recovers_while_three_other_servers_stay_available():
    crashing = Path(__file__).parents[1] / "support" / "crashing_server.py"
    pool = MCPToolPool(
        {
            "polymarket": _working_server(),
            "kalshi": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(crashing)],
            },
            "sports_state": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "market_agent.mcp.sports_state"],
            },
            "tavily": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "market_agent.mcp.tavily"],
            },
        }
    )
    await pool.start()
    try:
        async with pool.tools() as tools:
            crash = next(tool for tool in tools if tool.name == "kalshi_crash")
            with pytest.raises(McpError, match="Connection closed"):
                await crash.ainvoke(
                    {"type": "tool_call", "name": "kalshi_crash", "args": {}, "id": "die"}
                )
            pool.transport_failed("kalshi")

        assert pool.available_sources() == ["polymarket", "sports_state", "tavily"]
        async with pool.tools() as tools:
            market = next(tool for tool in tools if tool.name == "polymarket_get_market")
            result = await market.ainvoke(
                {
                    "type": "tool_call",
                    "name": "polymarket_get_market",
                    "args": {"market_id": "561229"},
                    "id": "healthy",
                }
            )
            assert isinstance(result, ToolMessage) and result.status == "success"

        async with asyncio.timeout(8):
            while "kalshi" not in pool.available_sources():
                async with pool.tools():
                    pass
                await asyncio.sleep(0.05)
        async with pool.tools() as tools:
            ping = next(tool for tool in tools if tool.name == "kalshi_ping")
            result = await ping.ainvoke(
                {"type": "tool_call", "name": "kalshi_ping", "args": {}, "id": "recovered"}
            )
            assert isinstance(result, ToolMessage) and result.status == "success"
    finally:
        await pool.close()
