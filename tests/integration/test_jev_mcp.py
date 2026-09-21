import json

import httpx
import pytest
from jsonschema import validate
from mcp.shared.memory import create_connected_server_and_client_session

from market_agent.jev import JevReviewer
from market_agent.mcp.common import MarketDetail
from market_agent.mcp.jev import create_server

pytestmark = pytest.mark.integration

LEFT_RULES = (
    "An abandoned game remains open until it is resumed and completed. Overtime is included."
)
RIGHT_RULES = "An abandoned game is void and all positions are refunded. Overtime is included."


def detail(platform: str, *, rules: str) -> MarketDetail:
    is_poly = platform == "polymarket"
    participants = ["Carolina Panthers", "Atlanta Falcons"]
    return MarketDetail.model_validate(
        {
            "platform": platform,
            "market_id": "poly-1" if is_poly else "KXNFL-1",
            "title": "Panthers vs Falcons" if is_poly else "Panthers win",
            "status": "open",
            "yes_price": "0.5",
            "no_price": "0.5",
            "yes_bid": None,
            "yes_ask": None,
            "close_time": "2026-09-20T17:00:00Z",
            "source_url": "https://example.test/market",
            "retrieved_at": "2026-09-20T16:00:00Z",
            "quote_as_of": None,
            "quote_is_stale": True,
            "event_id": "poly-event" if is_poly else "kalshi-event",
            "sports": {
                "league": "nfl",
                "provider_event_id": "poly-event" if is_poly else "kalshi-event",
                "raw_title": "Panthers vs Falcons",
                "participants": participants,
                "raw_participants": participants,
                "market_type": "game_winner",
                "scheduled_start": "2026-09-20T17:00:00Z",
            },
            "outcome_quotes": (
                [
                    {
                        "label": team,
                        "canonical_participant": team,
                        "price_kind": "provider_snapshot",
                    }
                    for team in participants
                ]
                if is_poly
                else [
                    {
                        "label": participants[0],
                        "side": "yes",
                        "canonical_participant": participants[0],
                        "price_kind": "last_trade",
                    },
                    {
                        "label": f"Not {participants[0]}",
                        "side": "no",
                        "price_kind": "derived_complement",
                    },
                ]
            ),
            "outcomes": participants if is_poly else ["Yes", "No"],
            "rules": rules,
            "rules_truncated": False,
            "resolution_source": "https://www.nfl.com/scores",
            "resolution_deadline": None,
        }
    )


def jev_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "answers": {
                "equivalence": {
                    "type": "choice",
                    "choice": "different",
                    "probabilities": {
                        "equivalent": 0.01,
                        "different": 0.94,
                        "ambiguous": 0.05,
                    },
                }
            },
            "usage": {"inputTokens": 500},
            "providerMetadata": {"typesafe": {"confidence": {"equivalence": 0.85}}},
        },
    )


async def test_jev_mcp_discovers_and_reviews_unchanged_market_details():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return jev_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", http_client=http)
        server = create_server(reviewer)
        async with create_connected_server_and_client_session(server) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {"jev_review_contracts"}
            schema = tools["jev_review_contracts"].inputSchema
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == {"polymarket_contract", "kalshi_contract"}

            result = await session.call_tool(
                "jev_review_contracts",
                {
                    "polymarket_contract": detail("polymarket", rules=LEFT_RULES).model_dump(
                        mode="json"
                    ),
                    "kalshi_contract": detail("kalshi", rules=RIGHT_RULES).model_dump(mode="json"),
                },
            )

    assert not result.isError
    validate(result.structuredContent, tools["jev_review_contracts"].outputSchema)
    assert result.structuredContent["deterministic_assessment"]["verdict"] == "ambiguous"
    assert result.structuredContent["final_assessment"]["verdict"] == "different"
    assert result.structuredContent["jev_called"] is True
    assert len(requests) == 1
    state = json.loads(requests[0].content)["state"]
    assert "yes_price" not in json.dumps(state)


async def test_jev_mcp_preserves_deterministic_veto_without_calling_jev():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return jev_response()

    left = detail("polymarket", rules="If the game is cancelled, the market resolves 50-50.")
    right = detail("kalshi", rules="If the game is cancelled, the market resolves Yes.")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        server = create_server(JevReviewer("test-key", http_client=http))
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "jev_review_contracts",
                {
                    "polymarket_contract": left.model_dump(mode="json"),
                    "kalshi_contract": right.model_dump(mode="json"),
                },
            )

    assert not result.isError
    assert result.structuredContent["deterministic_assessment"]["verdict"] == "different"
    assert result.structuredContent["final_assessment"]["verdict"] == "different"
    assert result.structuredContent["jev_called"] is False
    assert calls == 0


async def test_jev_mcp_rejects_swapped_platform_inputs():
    server = create_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "jev_review_contracts",
            {
                "polymarket_contract": detail("kalshi", rules=LEFT_RULES).model_dump(mode="json"),
                "kalshi_contract": detail("polymarket", rules=RIGHT_RULES).model_dump(mode="json"),
            },
        )

    assert result.isError
    assert "platform_mismatch" in result.content[0].text


async def test_jev_mcp_reports_disabled_configuration_without_exposing_details(monkeypatch):
    class DisabledSettings:
        jev_enabled = False
        ai_gateway_api_key = None

    monkeypatch.setattr("market_agent.mcp.jev._JevMcpSettings", DisabledSettings)
    server = create_server()
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "jev_review_contracts",
            {
                "polymarket_contract": detail("polymarket", rules=LEFT_RULES).model_dump(
                    mode="json"
                ),
                "kalshi_contract": detail("kalshi", rules=RIGHT_RULES).model_dump(mode="json"),
            },
        )

    assert result.isError
    assert "jev_disabled" in result.content[0].text
    assert LEFT_RULES not in result.content[0].text
