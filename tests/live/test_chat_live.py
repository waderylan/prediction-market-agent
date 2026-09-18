"""Explicit real-model + public MCP checks; never part of the offline suite."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from market_agent.app import create_app
from market_agent.config import load_settings

pytestmark = [
    pytest.mark.live_smoke,
    pytest.mark.skipif(os.getenv("RUN_LIVE_AGENT") != "1", reason="set RUN_LIVE_AGENT=1"),
]


@pytest.fixture
def client():
    load_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    load_settings.cache_clear()


def ask(client, query, session):
    result = client.post("/chat", json={"query": query, "session_id": session})
    assert result.status_code == 200
    assert set(result.json()) == {"response"}
    text = result.json()["response"]
    assert text and "could not finish" not in text and "could not be connected" not in text
    return text


def events(caplog):
    return [r.safe_fields for r in caplog.records if r.message == "mcp_tool_finished"]


def test_live_no_tool_recall_isolation(client, caplog):
    caplog.set_level("INFO", logger="market_agent.agent")
    session = uuid4().hex
    ask(client, "Remember my research label: copper-lantern. Acknowledge briefly.", session)
    assert "copper-lantern" in ask(client, "What research label did I give you?", session).lower()
    assert (
        "copper-lantern"
        not in ask(client, "What research label did I give you?", uuid4().hex).lower()
    )
    assert not events(caplog)


def test_live_contract_readout(client, caplog):
    caplog.set_level("INFO", logger="market_agent.agent")
    text = ask(
        client,
        "Read Polymarket market 561229: its quoted price, what makes YES win, and one rule caveat.",
        uuid4().hex,
    )
    assert any(
        e["tool"] == "polymarket_get_market" and e["status"] == "success" for e in events(caplog)
    )
    assert "https://" in text


@pytest.mark.parametrize(
    "query",
    [
        "Find up to two open Polymarket contracts about Bitcoin. Show candidates, not a forecast.",
        "What Polymarket contracts cover the 2028 presidential election? Find a couple.",
    ],
)
def test_live_search_phrasings(client, caplog, query):
    caplog.set_level("INFO", logger="market_agent.agent")
    ask(client, query, uuid4().hex)
    assert any(
        e["tool"] == "polymarket_search_markets" and e["status"] == "success"
        for e in events(caplog)
    )
    assert len(events(caplog)) <= 4
