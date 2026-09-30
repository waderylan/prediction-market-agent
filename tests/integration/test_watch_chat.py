"""Conversational watch commands use the same durable service as the runner."""

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr
from test_chat import ScriptedModel

from market_agent.agent import ChatAgent
from market_agent.app import create_app
from market_agent.watch.coordinator import WatchCoordinator
from market_agent.watch.delivery import DeliveryWorker, TelegramDelivery
from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    GameIdentity,
    MarketIdentity,
    PriceMoveCondition,
    WatchObservation,
    WatchRule,
)
from market_agent.watch.repository import SQLiteWatchRepository
from market_agent.watch.service import WatchService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "watch" / "yankees_scoring_replay.json"


@asynccontextmanager
async def no_data_tools():
    yield []


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


class FixtureTool(BaseTool):
    payload: dict = Field(default_factory=dict)

    def _run(self, *args, **kwargs):
        raise AssertionError("Only async tool invocation is expected")

    async def ainvoke(self, input, config=None, **kwargs):
        return ToolMessage(
            "fixture evidence",
            tool_call_id=input["id"],
            artifact={"structured_content": self.payload},
        )


class GameRefArgs(BaseModel):
    game_ref: str


class MarketIdArgs(BaseModel):
    market_id: str


def exact_source_tools() -> list[BaseTool]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    game = fixture["game"]
    market = fixture["markets"][0]
    start = game["scheduled_start"]
    teams = [game["home_team"], game["away_team"]]
    state = {
        "league": "mlb",
        "game_ref": game["game_ref"],
        "source": "espn",
        "provider_game_id": "fixture",
        "home_team": teams[0],
        "away_team": teams[1],
        "raw_home_team": teams[0],
        "raw_away_team": teams[1],
        "scheduled_start": start,
        "timezone": "America/Los_Angeles",
        "local_date": "2026-09-23",
        "scheduled_start_local": start,
        "lifecycle": "live",
        "retrieved_at": "2026-09-24T00:00:02Z",
        "observation_id": "fixture-state",
        "source_url": "https://example.test/game",
        "situation": {"sport": "baseball", "phase": "unavailable"},
    }
    detail = {
        "platform": "kalshi",
        "market_id": market["market_id"],
        "title": market["title"],
        "status": "open",
        "yes_price": "0.42",
        "no_price": "0.58",
        "yes_bid": None,
        "yes_ask": None,
        "close_time": None,
        "source_url": "https://example.test/market",
        "retrieved_at": "2026-09-24T00:00:02Z",
        "quote_as_of": None,
        "quote_freshness": "timestamp_unavailable",
        "quote_freshness_reason": "fixture",
        "quote_is_stale": False,
        "outcome_quotes": [
            {
                "label": teams[0],
                "canonical_participant": teams[0],
                "price_kind": "provider_snapshot",
                "price": "0.42",
            }
        ],
        "outcomes": [teams[0], teams[1]],
        "rules": None,
        "rules_truncated": False,
        "resolution_source": None,
        "resolution_deadline": None,
        "sports": {
            "league": "mlb",
            "provider_event_id": "fixture",
            "raw_title": market["title"],
            "participants": teams,
            "raw_participants": teams,
            "scheduled_start": start,
        },
    }
    return [
        FixtureTool(
            name="sports_state_get_game_state",
            description="Read one exact game state.",
            args_schema=GameRefArgs,
            payload=state,
        ),
        FixtureTool(
            name="kalshi_get_market",
            description="Read one exact Kalshi contract.",
            args_schema=MarketIdArgs,
            payload=detail,
        ),
    ]


def watched_rule(session: str = "owner") -> WatchRule:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return WatchRule(
        watch_id="watch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        session_id=session,
        confirmed_at=datetime(2026, 9, 24, tzinfo=UTC),
        creation_source="langgraph",
        game=GameIdentity.model_validate_json(json.dumps(fixture["game"])),
        markets=[MarketIdentity.model_validate_json(json.dumps(fixture["markets"][0]))],
        conditions=[
            PriceMoveCondition(
                condition_id="move",
                threshold=Decimal("0.08"),
                window_seconds=120,
                event_relationship="any",
            )
        ],
        delivery=DeliveryPolicy(
            channels=frozenset({DeliveryChannel.INBOX, DeliveryChannel.TELEGRAM})
        ),
    )


def test_chat_reads_and_manages_watch_with_versioned_delete(tmp_path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    rule = watched_rule()
    repository.save_rule(rule, due_at=rule.monitoring_starts_at)
    service = WatchService(repository)
    watch_id = rule.watch_id
    model = ScriptedModel(
        replies=[
            call("watch_list", {}, "list"),
            AIMessage("incorrect monitoring claim"),
            call("watch_inspect", {"watch_id": watch_id}, "inspect"),
            AIMessage("incorrect"),
            call("watch_pause", {"watch_id": watch_id}, "pause"),
            AIMessage("incorrect"),
            call("watch_resume", {"watch_id": watch_id}, "resume"),
            AIMessage("incorrect"),
            call("watch_delete_preview", {"watch_id": watch_id}, "preview"),
            AIMessage("incorrect"),
            call("watch_delete_confirm", {"watch_id": watch_id}, "delete"),
            AIMessage("incorrect"),
        ]
    )
    with TestClient(create_app(ChatAgent(model, no_data_tools, watch_service=service))) as client:

        def ask(query: str) -> str:
            response = client.post("/chat/inspect", json={"query": query, "session_id": "owner"})
            assert response.status_code == 200
            assert response.json()["activity"][-1]["status"] == "success", response.json()
            return response.json()["response"]

        listing = ask("What are you watching?")
        assert watch_id in listing and "awaiting_first_poll" in listing
        inspection = ask(f"Inspect {watch_id}")
        assert "observed runtime awaiting_first_poll" in inspection
        assert "last success none" in inspection
        assert "paused" in ask("Pause it")
        assert service.runtime("owner", watch_id).next_due_at is None
        assert "awaiting_first_poll" in ask("Resume it")
        preview = ask("Delete it")
        assert f"confirm delete {watch_id}" in preview
        assert repository.get_rule(watch_id, "owner") is not None
        assert "deleted" in ask(f"confirm delete {watch_id}")
        assert repository.get_rule(watch_id, "owner") is None


def test_chat_rejects_cross_session_and_text_only_confirmation(tmp_path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    rule = watched_rule()
    repository.save_rule(rule, due_at=rule.monitoring_starts_at)
    service = WatchService(repository)
    model = ScriptedModel(
        replies=[
            call("watch_pause", {"watch_id": rule.watch_id}, "foreign-pause"),
            AIMessage("Done"),
            call("watch_confirm", {"draft_id": "draft_" + "a" * 32}, "no-preview"),
            AIMessage("Done"),
        ]
    )
    with TestClient(create_app(ChatAgent(model, no_data_tools, watch_service=service))) as client:
        foreign = client.post(
            "/chat", json={"query": f"Pause {rule.watch_id}", "session_id": "other"}
        )
        invalid = client.post("/chat", json={"query": "Confirm", "session_id": "owner"})
    assert "not completed" in foreign.json()["response"]
    assert "not completed" in invalid.json()["response"]
    assert service.inspect("owner", rule.watch_id).status.value == "active"


def test_stale_revision_and_delete_preview_cannot_mutate(tmp_path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    rule = watched_rule()
    repository.save_rule(rule, due_at=rule.monitoring_starts_at)
    service = WatchService(repository)
    preview = service.preview(
        session_id="owner",
        creation_source="langgraph",
        game=rule.game,
        markets=rule.markets,
        conditions=rule.conditions,
        delivery=rule.delivery,
        replaces_watch_id=rule.watch_id,
    )
    model = ScriptedModel(
        replies=[
            call("watch_delete_preview", {"watch_id": rule.watch_id}, "preview"),
            AIMessage("done"),
            call("watch_delete_confirm", {"watch_id": rule.watch_id}, "confirm"),
            AIMessage("done"),
        ]
    )
    with TestClient(create_app(ChatAgent(model, no_data_tools, watch_service=service))) as client:
        shown = client.post(
            "/chat", json={"query": f"Delete {rule.watch_id}", "session_id": "owner"}
        )
        assert "confirm delete" in shown.json()["response"]
        assert service.pause("owner", rule.watch_id)
        with pytest.raises(ValueError, match="changed since preview"):
            service.confirm("owner", preview.draft_id, expected_fingerprint=preview.fingerprint)
        rejected = client.post(
            "/chat", json={"query": f"confirm delete {rule.watch_id}", "session_id": "owner"}
        )
    assert "not completed" in rejected.json()["response"]
    assert service.inspect("owner", rule.watch_id).status.value == "paused"


def test_chat_preview_confirm_is_exact_and_idempotent(tmp_path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    service = WatchService(repository)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    game_ref = fixture["game"]["game_ref"]
    market_id = fixture["markets"][0]["market_id"]
    model = ScriptedModel(
        replies=[
            call("sports_state_get_game_state", {"game_ref": game_ref}, "game"),
            call("kalshi_get_market", {"market_id": market_id}, "market"),
            call(
                "watch_preview",
                {
                    "game_ref": game_ref,
                    "markets": [
                        {
                            "platform": "kalshi",
                            "market_id": market_id,
                            "outcome": "New York Yankees",
                        }
                    ],
                    "conditions": [
                        {
                            "kind": "price_move",
                            "condition_id": "move",
                            "threshold_points": 8,
                            "window_seconds": 120,
                            "event_relationship": "any",
                        }
                    ],
                    "telegram": True,
                },
                "preview",
            ),
            AIMessage("Watch is monitoring now"),
        ]
    )

    @asynccontextmanager
    async def sources():
        yield exact_source_tools()

    with TestClient(create_app(ChatAgent(model, sources, watch_service=service))) as client:

        def ask(query: str, session: str = "owner") -> str:
            response = client.post("/chat/inspect", json={"query": query, "session_id": session})
            assert response.status_code == 200
            return response.json()["response"]

        preview = ask("Watch the Yankees price move 8 points and send Telegram alerts")
        assert "Watch preview" in preview
        assert repository.list_rules("owner") == []
        draft_id = preview.split("Watch preview `", 1)[1].split("`", 1)[0]
        model.replies.extend(
            [
                call("watch_confirm", {"draft_id": draft_id}, "wrong-session"),
                AIMessage("saved"),
                call("watch_confirm", {"draft_id": draft_id}, "right-session"),
                AIMessage("monitoring"),
                call("watch_confirm", {"draft_id": draft_id}, "replay"),
                AIMessage("saved"),
            ]
        )
        assert "not completed" in ask(f"confirm {draft_id}", "other")
        confirmation = ask(f"confirm {draft_id}")
        assert "saved" in confirmation and "awaiting_first_poll" in confirmation
        assert len(repository.list_rules("owner")) == 1
        assert "saved" in ask(f"confirm {draft_id}")
        assert len(repository.list_rules("owner")) == 1

        model.replies.extend(
            [
                call("sports_state_get_game_state", {"game_ref": game_ref}, "second-game"),
                call("kalshi_get_market", {"market_id": market_id}, "second-market"),
                call(
                    "watch_preview",
                    {
                        "game_ref": game_ref,
                        "markets": [
                            {
                                "platform": "kalshi",
                                "market_id": market_id,
                                "outcome": "New York Yankees",
                            }
                        ],
                        "conditions": [
                            {
                                "kind": "price_move",
                                "condition_id": "move",
                                "threshold_points": 6,
                                "window_seconds": 120,
                                "event_relationship": "any",
                            }
                        ],
                    },
                    "second-preview",
                ),
                AIMessage("done"),
            ]
        )
        second = ask("Preview another Yankees price watch")
        second_id = second.split("Watch preview `", 1)[1].split("`", 1)[0]
        model.replies.extend(
            [
                call("watch_cancel_preview", {"draft_id": second_id}, "cancel"),
                AIMessage("done"),
            ]
        )
        assert "cancelled" in ask("Cancel that")
        assert len(repository.list_rules("owner")) == 1


@pytest.mark.live_smoke
@pytest.mark.skipif(
    not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    reason="set Telegram credentials",
)
async def test_chat_runner_telegram_lifecycle_live(tmp_path) -> None:
    """One local chat lifecycle using recorded evidence and a real Telegram delivery."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    first = WatchObservation.model_validate_json(json.dumps(fixture["timeline"][0]))
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    service = WatchService(repository, telegram_configured=True)
    game_ref = fixture["game"]["game_ref"]
    market_id = fixture["markets"][0]["market_id"]
    model = ScriptedModel(replies=[])
    session = f"chat-live-{uuid4().hex}"

    @asynccontextmanager
    async def sources():
        yield exact_source_tools()

    agent = ChatAgent(model, sources, watch_service=service)
    with TestClient(create_app(agent)) as client:

        def ask(query: str, name: str | None = None, args: dict | None = None) -> str:
            if name:
                model.replies.extend([call(name, args or {}, uuid4().hex), AIMessage("done")])
            response = client.post("/chat/inspect", json={"query": query, "session_id": session})
            assert response.status_code == 200
            if name:
                assert response.json()["activity"][-1]["status"] == "success", response.json()
            return response.json()["response"]

        preview_args = {
            "game_ref": game_ref,
            "markets": [
                {"platform": "kalshi", "market_id": market_id, "outcome": "New York Yankees"}
            ],
            "conditions": [
                {
                    "kind": "price_move",
                    "condition_id": "move",
                    "threshold_points": 8,
                    "window_seconds": 120,
                    "event_relationship": "any",
                }
            ],
            "telegram": True,
        }
        model.replies.extend(
            [
                call("sports_state_get_game_state", {"game_ref": game_ref}, uuid4().hex),
                call("kalshi_get_market", {"market_id": market_id}, uuid4().hex),
                call("watch_preview", preview_args, uuid4().hex),
                AIMessage("done"),
            ]
        )
        preview = ask("Watch that game and alert me at 8 points")
        assert "Watch preview `" in preview, preview
        draft_id = preview.split("Watch preview `", 1)[1].split("`", 1)[0]
        assert repository.list_rules(session) == []
        saved = ask(f"confirm {draft_id}", "watch_confirm", {"draft_id": draft_id})
        assert "awaiting_first_poll" in saved
        watch_id = repository.list_rules(session)[0].watch_id

        class Evidence:
            async def observe(self, rule: WatchRule, now: datetime) -> WatchObservation:
                return first

        poll = await WatchCoordinator(repository, Evidence()).poll(
            owner="foreground-chat-live", now=first.retrieved_at
        )
        assert poll.lifecycle_events == 1
        delivery = TelegramDelivery(
            SecretStr(os.environ["TELEGRAM_BOT_TOKEN"]),
            SecretStr(os.environ["TELEGRAM_CHAT_ID"]),
        )
        assert (
            await DeliveryWorker(repository, delivery).run_once(
                "chat-live-delivery", first.retrieved_at
            )
            == 1
        )
        status = ask(f"Inspect {watch_id}", "watch_inspect", {"watch_id": watch_id})
        assert "monitoring" in status
        inbox = ask("Show my alerts", "watch_inbox", {})
        assert "monitoring_started" in inbox and "sent" in inbox
        event = repository.list_events(session)[0]
        delivered = ask(
            f"Did Telegram send event {event.event_id}?",
            "watch_delivery_status",
            {"event_id": event.event_id},
        )
        assert "sent" in delivered

        preview_args["replaces_watch_id"] = watch_id
        preview_args["conditions"][0]["threshold_points"] = 6
        model.replies.extend(
            [
                call("sports_state_get_game_state", {"game_ref": game_ref}, uuid4().hex),
                call("kalshi_get_market", {"market_id": market_id}, uuid4().hex),
                call("watch_preview", preview_args, uuid4().hex),
                AIMessage("done"),
            ]
        )
        revised_preview = ask("Change it to 6 points")
        revision_id = revised_preview.split("Watch preview `", 1)[1].split("`", 1)[0]
        assert "probability points" in revised_preview
        assert "saved" in ask(f"confirm {revision_id}", "watch_confirm", {"draft_id": revision_id})
        assert repository.get_rule(watch_id, session).conditions[0].threshold == Decimal("0.06")
        assert "paused" in ask("Pause it", "watch_pause", {"watch_id": watch_id})
        assert "awaiting_first_poll" in ask("Resume it", "watch_resume", {"watch_id": watch_id})
        assert "paused" in ask("Pause it", "watch_pause", {"watch_id": watch_id})
        assert "confirm delete" in ask("Delete it", "watch_delete_preview", {"watch_id": watch_id})
        assert "deleted" in ask(
            f"confirm delete {watch_id}", "watch_delete_confirm", {"watch_id": watch_id}
        )
        assert repository.get_rule(watch_id, session) is None


@pytest.mark.live_smoke
@pytest.mark.skipif(os.getenv("RUN_LIVE_WATCH_AGENT") != "1", reason="opt in to model eval")
def test_real_model_watch_status_and_management(tmp_path) -> None:
    """Evaluate actual model tool choice through /chat with a local seeded watch."""
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    rule = watched_rule()
    repository.save_rule(rule, due_at=rule.monitoring_starts_at)
    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5.6-sol"),
        api_key=SecretStr(os.getenv("OPENAI_API_KEY", "local-codex-placeholder")),
        base_url=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8091/v1"),
        timeout=120,
        max_retries=0,
        use_responses_api=False,
    )
    agent = ChatAgent(
        model, no_data_tools, model_timeout=120, watch_service=WatchService(repository)
    )
    with TestClient(create_app(agent)) as client:

        def ask(query: str) -> dict:
            response = client.post("/chat/inspect", json={"query": query, "session_id": "owner"})
            assert response.status_code == 200
            return response.json()

        listing = ask("What active watches do I have?")
        assert rule.watch_id in listing["response"]
        assert listing["activity"][-1]["tool"] == "watch_list"
        status = ask("Is my Yankees watch actually monitoring yet?")
        assert "awaiting_first_poll" in status["response"]
        assert status["activity"][-1]["tool"] == "watch_inspect"
        pause = ask("Pause that Yankees watch")
        assert "paused" in pause["response"]
        resume = ask("Resume it")
        assert "awaiting_first_poll" in resume["response"]
        delete_preview = ask("Delete that Yankees watch")
        assert "confirm delete" in delete_preview["response"]
        deleted = ask(f"confirm delete {rule.watch_id}")
        assert "deleted" in deleted["response"]
        assert repository.get_rule(rule.watch_id, "owner") is None


@pytest.mark.live_smoke
@pytest.mark.skipif(os.getenv("RUN_LIVE_WATCH_AGENT") != "1", reason="opt in to model eval")
def test_real_model_watch_preview_and_confirmation(tmp_path) -> None:
    """Actual model resolves exact detail, previews, and confirms only after a second turn."""
    repository = SQLiteWatchRepository(tmp_path / "watch.db")
    service = WatchService(repository)
    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-5.6-sol"),
        api_key=SecretStr(os.getenv("OPENAI_API_KEY", "local-codex-placeholder")),
        base_url=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8091/v1"),
        timeout=120,
        max_retries=0,
        use_responses_api=False,
    )

    @asynccontextmanager
    async def sources():
        yield exact_source_tools()

    agent = ChatAgent(model, sources, model_timeout=120, watch_service=service)
    with TestClient(create_app(agent)) as client:
        query = (
            "Preview a watch for exact MLB game_ref fixture-game-ref-yankees-red-sox and "
            "exact Kalshi market KXMLBGAME-FIXTURE-NYY, New York Yankees outcome. Alert if its "
            "price moves 8 probability points within 120 seconds, with any scoring relationship. "
            "Telegram off. Resolve exact game and market detail with available tools before "
            "previewing. Do not confirm yet."
        )
        first = client.post("/chat/inspect", json={"query": query, "session_id": "owner"})
        assert first.status_code == 200
        assert "Watch preview `" in first.json()["response"], first.json()
        assert repository.list_rules("owner") == []
        draft_id = first.json()["response"].split("Watch preview `", 1)[1].split("`", 1)[0]
        confirmed = client.post(
            "/chat/inspect", json={"query": f"confirm {draft_id}", "session_id": "owner"}
        )
        assert confirmed.status_code == 200
        assert "awaiting_first_poll" in confirmed.json()["response"], confirmed.json()
        assert len(repository.list_rules("owner")) == 1
