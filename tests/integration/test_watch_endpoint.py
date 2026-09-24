"""Authenticated scheduler boundary without live Google or provider services."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from test_chat import ScriptedModel

from market_agent.agent import ChatAgent
from market_agent.app import create_app
from market_agent.watch.models import PollResult
from market_agent.watch.repository import SQLiteWatchRepository
from market_agent.watch.service import WatchService

pytestmark = pytest.mark.integration


class FakeAgent:
    async def chat(self, query, session_id, **kwargs):
        return "unused"

    async def chat_detailed(self, query, session_id, **kwargs):
        raise AssertionError("unused")


class FakeOIDC:
    def verify(self, authorization):
        return authorization == "Bearer valid"


class FakeCoordinator:
    calls = 0
    owner = ""

    async def poll(self, *, owner):
        self.calls += 1
        self.owner = owner
        return PollResult(claimed_watches=2, observation_groups=1, created_triggers=1)


class FakeDelivery:
    calls = 0
    owner = ""

    async def run_once(self, owner):
        self.calls += 1
        self.owner = owner
        return 1


def test_scheduler_endpoint_requires_verified_oidc_and_preserves_chat_contract() -> None:
    coordinator = FakeCoordinator()
    delivery = FakeDelivery()
    runtime = SimpleNamespace(oidc=FakeOIDC(), coordinator=coordinator, delivery=delivery)
    with TestClient(create_app(FakeAgent(), runtime)) as client:  # type: ignore[arg-type]
        rejected = client.post("/internal/watches/poll", headers={"Authorization": "Bearer wrong"})
        accepted = client.post("/internal/watches/poll", headers={"Authorization": "Bearer valid"})
        chat = client.post("/chat", json={"query": "hello", "session_id": "session"})
    assert rejected.status_code == 401
    assert accepted.json() == {
        "claimed_watches": 2,
        "observation_groups": 1,
        "created_triggers": 1,
        "duplicate_triggers": 0,
        "source_warnings": 0,
        "delivery_attempts": 1,
        "model_calls": 0,
        "tavily_calls": 0,
    }
    assert chat.json() == {"response": "unused"}
    assert coordinator.calls == delivery.calls == 1
    invocation = coordinator.owner.removeprefix("cloud-")
    assert len(invocation) == 32
    assert delivery.owner == f"cloud-delivery-{invocation}"


async def test_host_watch_management_survives_complete_mcp_outage(tmp_path) -> None:
    @asynccontextmanager
    async def no_mcp_tools():
        yield []

    service = WatchService(SQLiteWatchRepository(tmp_path / "watches.db"))
    model = ScriptedModel(
        replies=[
            AIMessage(
                "",
                tool_calls=[{"name": "watch_list", "args": {}, "id": "watch-list"}],
            ),
            AIMessage("No watches yet."),
        ]
    )
    turn = await ChatAgent(model, no_mcp_tools, watch_service=service).chat_detailed(
        "List my watches", "outage-session"
    )
    assert turn.response == "No watches yet."
    assert len(turn.activity) == 1
    assert turn.activity[0].server == "watch_host"
    assert turn.activity[0].status == "success"
