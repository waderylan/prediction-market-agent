import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from test_chat import ScriptedModel, tool_call

from market_agent.agent import ChatAgent

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("missing", [False, True])
async def test_graph_with_real_stdio_process(missing):
    script = Path(__file__).parents[1] / "support" / "polymarket_server.py"
    adapter = MultiServerMCPClient(
        {
            "polymarket": {
                "transport": "stdio",
                "command": "nonexistent-market-server" if missing else sys.executable,
                "args": [str(script)],
            }
        }
    )

    @asynccontextmanager
    async def connect():
        async with adapter.session("polymarket") as session:
            yield await load_mcp_tools(session)

    model = ScriptedModel(replies=[tool_call(), AIMessage("Verified subprocess")])
    result = await ChatAgent(model, connect).chat("Read market", "stdio")
    if missing:
        assert "could not be connected" in result
    else:
        assert result == "Verified subprocess"
        assert model.observed[-1][-1].status == "success"
