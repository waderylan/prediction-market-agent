"""Real sports-state MCP discovery, schemas, calls, errors, and agent consumption."""

import json
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy

import httpx
import pytest
from jsonschema import validate
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared.memory import create_connected_server_and_client_session
from test_chat import ScriptedModel, tool_call
from test_sports_mcp import connection as market_connection

from market_agent.agent import ChatAgent
from market_agent.mcp.sports_state import create_server
from market_agent.providers.game_state import SportsStateClient

pytestmark = pytest.mark.integration


def _team(team_id, display, abbreviation):
    city, nickname = display.rsplit(" ", 1)
    return {
        "id": team_id,
        "displayName": display,
        "shortDisplayName": nickname,
        "name": nickname,
        "abbreviation": abbreviation,
        "location": city,
    }


def _status():
    return {
        "displayClock": "7:21",
        "period": 3,
        "type": {
            "name": "STATUS_IN_PROGRESS",
            "state": "in",
            "completed": False,
            "description": "In Progress",
            "detail": "7:21 - 3rd Quarter",
        },
    }


def _event():
    situation = {
        "down": 2,
        "distance": 7,
        "possession": "29",
        "possessionText": "ATL 21",
        "isRedZone": False,
        "homeTimeouts": 3,
        "awayTimeouts": 2,
        "downDistanceText": "2nd & 7 at ATL 21",
        "lastPlay": {"text": "Pass complete."},
    }
    competition = {
        "id": "401000001",
        "date": "2026-09-20T17:00:00Z",
        "status": _status(),
        "competitors": [
            {
                "homeAway": "home",
                "score": "10",
                "team": _team("1", "Atlanta Falcons", "ATL"),
            },
            {
                "homeAway": "away",
                "score": "17",
                "team": _team("29", "Carolina Panthers", "CAR"),
            },
        ],
        "situation": situation,
    }
    return {
        "id": "401000001",
        "date": "2026-09-20T17:00:00Z",
        "status": _status(),
        "competitions": [competition],
    }


def _summary():
    event = _event()
    return {"header": {"id": event["id"], "competitions": event["competitions"]}}


def _mlb_event():
    event = _event()
    event["id"] = "401000002"
    event["date"] = "2026-09-20T00:10:00Z"
    competition = event["competitions"][0]
    competition["id"] = event["id"]
    competition["date"] = event["date"]
    competition["competitors"] = [
        {
            "homeAway": "home",
            "score": "3",
            "team": _team("10", "New York Yankees", "NYY"),
        },
        {
            "homeAway": "away",
            "score": "2",
            "team": _team("25", "San Diego Padres", "SD"),
        },
    ]
    competition["status"]["period"] = 6
    competition["status"]["type"]["detail"] = "Bottom 6th"
    event["status"] = competition["status"]
    return event


def _mlb_summary():
    event = _mlb_event()
    return {
        "header": {"id": event["id"], "competitions": event["competitions"]},
        "situation": {"balls": 1, "strikes": 2, "outs": 1},
    }


@asynccontextmanager
async def protocol(mode="success", league="nfl"):
    calls = []

    def handler(request):
        calls.append(request)
        if mode == "403":
            return httpx.Response(403, text="sensitive diagnostic")
        if mode == "malformed" and request.url.path.endswith("/summary"):
            return httpx.Response(200, json={"header": False})
        if request.url.path.endswith("/summary"):
            payload = _mlb_summary() if league == "mlb" else _summary()
            if mode == "identity":
                payload = deepcopy(payload)
                payload["header"]["id"] = "999999999"
            return httpx.Response(200, json=payload)
        event = _mlb_event() if league == "mlb" else _event()
        return httpx.Response(200, json={"events": [event]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SportsStateClient(http_client=http, max_attempts=1)
        async with create_connected_server_and_client_session(create_server(client)) as session:
            yield session, calls


async def test_schemas_discovery_detail_and_cache_are_real_mcp_calls():
    async with protocol() as (session, calls):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"sports_state_find_games", "sports_state_get_game_state"}
        find_schema = tools["sports_state_find_games"].inputSchema
        assert find_schema["additionalProperties"] is False
        assert set(find_schema["required"]) == {"query", "league", "timezone"}
        assert find_schema["properties"]["limit"]["minimum"] == 1
        assert find_schema["properties"]["limit"]["maximum"] == 10
        assert find_schema["properties"]["compact"]["type"] == "boolean"
        assert "enum" in find_schema["properties"]["league"]
        detail_schema = tools["sports_state_get_game_state"].inputSchema
        assert detail_schema["additionalProperties"] is False
        assert detail_schema["required"] == ["game_ref"]

        bad = await session.call_tool(
            "sports_state_find_games",
            {"query": "Falcons", "league": "nfl", "timezone": "UTC", "limit": 20},
        )
        assert bad.isError and calls == []
        unclear = await session.call_tool(
            "sports_state_find_games",
            {"query": "OSU", "league": "ncaa_football", "timezone": "UTC"},
        )
        assert not unclear.isError and unclear.structuredContent["clarification"]
        assert unclear.structuredContent["discovery_mode"] == "clarification"
        assert 'query="Ohio State Buckeyes"' in unclear.structuredContent["clarification"]
        assert (
            unclear.structuredContent["suggested_queries"] == unclear.structuredContent["choices"]
        )
        assert calls == []
        compact = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "all",
                "league": "nfl",
                "timezone": "America/Los_Angeles",
                "local_date": "2026-09-20",
                "compact": True,
            },
        )
        assert not compact.isError
        assert compact.structuredContent["compact"] is True
        assert "raw_home_team" not in compact.structuredContent["games"][0]
        found = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "Falcons vs Panthers",
                "league": "nfl",
                "timezone": "America/Los_Angeles",
                "local_date": "2026-09-20",
                "limit": 2,
            },
        )
        assert not found.isError
        validate(found.structuredContent, tools["sports_state_find_games"].outputSchema)
        assert found.structuredContent["coverage"]["scoreboard_requests"] == 1
        assert found.structuredContent["coverage"]["utc_boundary_check"] is False
        game = found.structuredContent["games"][0]
        assert game["local_date"] == "2026-09-20"
        assert game["source"] == "espn"
        assert game["game_ref"] != game["provider_game_id"]
        assert "lightweight scoreboard snapshot" in found.structuredContent["usage_note"]

        detail = await session.call_tool(
            "sports_state_get_game_state", {"game_ref": game["game_ref"]}
        )
        assert not detail.isError
        validate(detail.structuredContent, tools["sports_state_get_game_state"].outputSchema)
        assert detail.structuredContent["situation"]["sport"] == "football"
        assert detail.structuredContent["situation"]["possession_team"] == "Carolina Panthers"
        assert detail.structuredContent["situation"]["down"] == 2
        assert (
            "authoritative normalized sporting-state snapshot"
            in detail.structuredContent["usage_note"]
        )
        cached = await session.call_tool(
            "sports_state_get_game_state", {"game_ref": game["game_ref"]}
        )
        assert cached.structuredContent["cache_hit"] is True
        assert (
            cached.structuredContent["observation_id"] == detail.structuredContent["observation_id"]
        )
        assert sum(request.url.path.endswith("/summary") for request in calls) == 1


async def test_invalid_reference_is_stable_error_before_provider_io():
    async with protocol() as (session, calls):
        result = await session.call_tool("sports_state_get_game_state", {"game_ref": "401000001"})
        assert result.isError
        text = result.content[0].text
        assert '"code":"invalid_game_ref"' in text
        assert '"game_ref":"invalid"' in text
        assert calls == []


@pytest.mark.parametrize(
    "mode,code", [("403", "provider_unavailable"), ("malformed", "malformed_response")]
)
async def test_provider_and_malformed_errors_are_controlled(mode, code):
    async with protocol(mode) as (session, _):
        found = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "Falcons",
                "league": "nfl",
                "timezone": "UTC",
                "local_date": "2026-09-20",
            },
        )
        if mode == "403":
            assert found.isError and code in str(found)
            assert "sensitive diagnostic" not in str(found)
            return
        ref = found.structuredContent["games"][0]["game_ref"]
        detail = await session.call_tool("sports_state_get_game_state", {"game_ref": ref})
        assert detail.isError
        # NFL has no fallback; malformed response remains explicit.
        assert code in str(detail)
        assert (await session.list_tools()).tools


async def test_identity_conflict_is_controlled_and_session_survives():
    async with protocol("identity") as (session, _):
        found = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "Falcons",
                "league": "nfl",
                "timezone": "UTC",
                "local_date": "2026-09-20",
            },
        )
        ref = found.structuredContent["games"][0]["game_ref"]
        detail = await session.call_tool("sports_state_get_game_state", {"game_ref": ref})
        assert detail.isError and "response_identity_mismatch" in str(detail)
        assert len((await session.list_tools()).tools) == 2


async def test_agent_selects_game_state_tools_and_enforces_result_notice():
    @asynccontextmanager
    async def connect():
        async with protocol() as (session, _):
            yield await load_mcp_tools(session)

    async with protocol() as (session, _):
        found = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "Falcons",
                "league": "nfl",
                "timezone": "UTC",
                "local_date": "2026-09-20",
            },
        )
        game_ref = found.structuredContent["games"][0]["game_ref"]
    model = ScriptedModel(
        replies=[
            tool_call(
                "sports_state_find_games",
                {
                    "query": "Falcons",
                    "league": "nfl",
                    "timezone": "UTC",
                    "local_date": "2026-09-20",
                },
                "1",
            ),
            tool_call("sports_state_get_game_state", {"game_ref": game_ref}, "2"),
            AIMessage("Falcons trail 17-10 in the third quarter."),
        ]
    )
    response = await ChatAgent(model, connect).chat("What's the Falcons score?", "state")
    assert response.startswith("- Game state: espn observed at ")
    assert "does not establish prediction-market settlement" in response
    assert response.endswith("Falcons trail 17-10 in the third quarter.")
    messages = [message for message in model.observed[-1] if isinstance(message, ToolMessage)]
    assert len(messages) == 2 and all(message.status == "success" for message in messages)
    assert json.loads(messages[-1].content)["game_state"]["source"] == "espn"


async def test_irrelevant_question_uses_no_game_state_tool():
    @asynccontextmanager
    async def connect():
        async with protocol() as (session, _):
            yield await load_mcp_tools(session)

    model = ScriptedModel(replies=[AIMessage("A probability is a number from zero to one.")])
    turn = await ChatAgent(model, connect).chat_detailed("Explain probability", "no-state")
    assert turn.activity == []
    assert turn.response == "A probability is a number from zero to one."


async def test_agent_verifies_market_and_game_identity_before_combining():
    @asynccontextmanager
    async def connect():
        async with AsyncExitStack() as stack:
            market_session, _ = await stack.enter_async_context(market_connection("polymarket"))
            state_session, _ = await stack.enter_async_context(protocol(league="mlb"))
            yield [
                *(await load_mcp_tools(market_session)),
                *(await load_mcp_tools(state_session)),
            ]

    async with protocol(league="mlb") as (session, _):
        found = await session.call_tool(
            "sports_state_find_games",
            {
                "query": "Yankees vs Padres",
                "league": "mlb",
                "timezone": "UTC",
                "local_date": "2026-09-20",
            },
        )
        game_ref = found.structuredContent["games"][0]["game_ref"]
    model = ScriptedModel(
        replies=[
            tool_call("polymarket_search_markets", {"query": "Yankees vs Padres"}, "market-search"),
            tool_call("polymarket_get_market", {"market_id": "201"}, "market-detail"),
            tool_call(
                "sports_state_find_games",
                {
                    "query": "Yankees vs Padres",
                    "league": "mlb",
                    "timezone": "UTC",
                    "local_date": "2026-09-20",
                },
                "state-search",
            ),
            tool_call("sports_state_get_game_state", {"game_ref": game_ref}, "state-detail"),
            AIMessage("The state matches the identified game; settlement remains separate."),
        ]
    )
    response = await ChatAgent(model, connect).chat("Compare the market with the score", "joined")
    assert "does not establish prediction-market settlement" in response
    messages = [message for message in model.observed[-1] if isinstance(message, ToolMessage)]
    final_tool = json.loads(messages[-1].content)
    report = final_tool["matching_report"]["market_to_game"]
    assert report[0]["verdict"] == "match"
    assert {check["dimension"]: check["state"] for check in report[0]["checks"]} == {
        "league": "match",
        "participants": "match",
        "scheduled_start": "match",
        "provider_backed_game_reference": "match",
    }
    assert report[0]["game_source"] == "espn"
    assert report[0]["observed_at"]
    assert report[0]["use_together"] is True
    assert "does not establish contract equivalence" in report[0]["scope"]
