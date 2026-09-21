from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from jsonschema import validate
from mcp.shared.memory import create_connected_server_and_client_session

from market_agent.mcp.tavily import create_server
from market_agent.providers.research import TavilyResearchClient

pytestmark = pytest.mark.integration


def payload() -> dict:
    return {
        "query": "generated query",
        "request_id": "request-1",
        "results": [
            {
                "title": "Marlins vs Padres weather — September 20, 2026",
                "url": "https://weather.example/mlb-game",
                "content": "Miami Marlins visit the San Diego Padres on September 20, 2026.",
                "score": 0.88,
                "published_date": "Sun, 20 Sep 2026 15:00:00 GMT",
            }
        ],
    }


@asynccontextmanager
async def protocol(mode: str = "success"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if mode == "error":
            return httpx.Response(429, text="sensitive provider detail")
        if mode == "malformed":
            return httpx.Response(200, json={"query": "x", "results": False})
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        server = create_server(TavilyResearchClient(http_client=http))
        async with create_connected_server_and_client_session(
            server, read_timeout_seconds=timedelta(seconds=5)
        ) as session:
            yield session, calls


async def test_schema_and_typed_game_evidence_are_real_mcp_calls():
    async with protocol() as (session, calls):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"tavily_search_game_evidence"}
        schema = tools["tavily_search_game_evidence"].inputSchema
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "league",
            "team_a",
            "team_b",
            "game_date",
            "scheduled_start",
            "focus",
        }
        assert schema["properties"]["focus"]["enum"] == [
            "injuries",
            "lineups",
            "weather",
            "venue_or_schedule",
            "other_game_news",
        ]

        result = await session.call_tool(
            "tavily_search_game_evidence",
            {
                "league": "mlb",
                "team_a": "Miami Marlins",
                "team_b": "San Diego Padres",
                "game_date": "2026-09-20",
                "scheduled_start": "2026-09-20T20:10:00Z",
                "focus": "weather",
            },
        )

    assert len(calls) == 1
    assert not result.isError
    validate(result.structuredContent, tools["tavily_search_game_evidence"].outputSchema)
    assert result.structuredContent["provider"] == "tavily"
    assert result.structuredContent["sources"][0]["relationship"] == "same_matchup_date"
    assert result.structuredContent["sources"][0]["publication_date"].startswith("2026-09-20")


@pytest.mark.parametrize(
    "mode,code", [("error", "provider_error"), ("malformed", "malformed_response")]
)
async def test_provider_failures_are_controlled_and_session_survives(mode, code):
    async with protocol(mode) as (session, calls):
        arguments = {
            "league": "mlb",
            "team_a": "Miami Marlins",
            "team_b": "San Diego Padres",
            "game_date": "2026-09-20",
            "scheduled_start": "2026-09-20T20:10:00Z",
            "focus": "weather",
        }
        failed = await session.call_tool("tavily_search_game_evidence", arguments)
        assert failed.isError
        assert f'"code":"{code}"' in failed.content[0].text
        assert "sensitive provider detail" not in failed.content[0].text
        retried = await session.call_tool("tavily_search_game_evidence", arguments)
        assert retried.isError
        assert len(calls) == 2


async def test_invalid_inputs_are_rejected_before_tavily():
    async with protocol() as (session, calls):
        invalid = await session.call_tool(
            "tavily_search_game_evidence",
            {
                "league": "mlb",
                "team_a": "Miami Marlins",
                "team_b": "San Diego Padres",
                "game_date": "not-a-date",
                "scheduled_start": "2026-09-20T20:10:00Z",
                "focus": "everything",
                "extra": True,
            },
        )
    assert invalid.isError
    assert calls == []
