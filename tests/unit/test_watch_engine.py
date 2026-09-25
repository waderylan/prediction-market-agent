"""Meaningful contracts for watch validation, replay, persistence, and delivery."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from pydantic import SecretStr, ValidationError

from market_agent.providers.game_state import GamePlay, _GameRefPayload, encode_game_ref
from market_agent.watch.cloud import GoogleOIDCVerifier, GoogleSecretLoader
from market_agent.watch.coordinator import (
    MCPWatchEvidenceProvider,
    WatchCoordinator,
    _alert_worthy,
)
from market_agent.watch.delivery import (
    DeliveryWorker,
    TelegramDelivery,
    TelegramTransientError,
    TransportResponse,
)
from market_agent.watch.evaluator import build_trigger, evaluate_condition
from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    DivergenceCondition,
    GameIdentity,
    LifecycleCondition,
    MarketIdentity,
    OutboxStatus,
    PriceMoveCondition,
    ScoringPlay,
    SourceStatus,
    WatchObservation,
    WatchRule,
    WatchStatus,
)
from market_agent.watch.repository import FirestoreWatchRepository, SQLiteWatchRepository
from market_agent.watch.service import WatchService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "watch" / "yankees_scoring_replay.json"
BASE = datetime(2026, 9, 24, tzinfo=UTC)
REFERENCE_LA = _GameRefPayload(
    version=1,
    source="espn",
    league="mlb",
    event_id="401817071",
    scheduled_start=datetime(2026, 9, 23, 23, 5, tzinfo=UTC),
    timezone="America/Los_Angeles",
    home_team="New York Yankees",
    away_team="Boston Red Sox",
)


def replay() -> tuple[GameIdentity, list[MarketIdentity], list[WatchObservation]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return (
        GameIdentity.model_validate_json(json.dumps(payload["game"])),
        [MarketIdentity.model_validate_json(json.dumps(value)) for value in payload["markets"]],
        [WatchObservation.model_validate_json(json.dumps(value)) for value in payload["timeline"]],
    )


def condition(
    *, threshold: str = "0.08", relationship: str = "scoring_event"
) -> PriceMoveCondition:
    return PriceMoveCondition(
        condition_id=f"move_{relationship}",
        threshold=Decimal(threshold),
        window_seconds=120,
        event_relationship=relationship,  # type: ignore[arg-type]
        correlation_window_seconds=120,
        cooldown_seconds=300,
        rearm_below=Decimal("0.02"),
    )


def rule(*, telegram: bool = False, watch_id: str | None = None) -> WatchRule:
    game, markets, _ = replay()
    channels = {DeliveryChannel.INBOX}
    if telegram:
        channels.add(DeliveryChannel.TELEGRAM)
    return WatchRule(
        watch_id=watch_id or "watch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        session_id="session-a",
        confirmed_at=BASE - timedelta(hours=1),
        creation_source="cli",
        game=game,
        markets=markets,
        conditions=[condition()],
        delivery=DeliveryPolicy(channels=frozenset(channels)),
    )


def test_schema_rejects_relative_percent_shape_and_future_version() -> None:
    value = rule().model_dump(mode="json")
    value["schema_version"] = 2
    with pytest.raises(ValidationError):
        WatchRule.model_validate(value)
    with pytest.raises(ValidationError):
        PriceMoveCondition(
            condition_id="bad",
            threshold=Decimal("8"),
            window_seconds=120,
            event_relationship="any",
        )
    with pytest.raises(ValidationError):
        PriceMoveCondition(
            condition_id="zero",
            threshold=Decimal("0"),
            window_seconds=120,
            event_relationship="any",
        )
    value = rule().model_dump(mode="python")
    value["conditions"] = [DivergenceCondition(condition_id="spread", threshold=Decimal("0.05"))]
    value["markets"][1]["outcome"] = "Boston Red Sox"
    with pytest.raises(ValidationError, match="one canonical outcome"):
        WatchRule.model_validate(value)


def test_polling_selects_only_required_mcp_servers() -> None:
    assert MCPWatchEvidenceProvider.required_servers(rule()) == frozenset(
        {"sports_state", "kalshi", "polymarket"}
    )
    one_platform = rule().model_copy(update={"markets": rule().markets[:1]})
    assert MCPWatchEvidenceProvider.required_servers(one_platform) == frozenset(
        {"sports_state", "kalshi"}
    )


@pytest.mark.asyncio
async def test_polling_invokes_mcp_with_tool_call_envelope(monkeypatch) -> None:
    invocations: list[dict[str, Any]] = []

    class CapturingTool:
        async def ainvoke(self, invocation):
            invocations.append(invocation)
            return ToolMessage(
                "validated evidence",
                tool_call_id=invocation["id"],
                status="success",
                artifact={"structured_content": {}},
            )

    sentinel = object()
    monkeypatch.setattr(
        "market_agent.watch.coordinator._validate_tool_result",
        lambda name, arguments, result: sentinel,
    )
    provider = MCPWatchEvidenceProvider()
    result = await provider._invoke(
        {"sports_state_get_game_state": CapturingTool()},  # type: ignore[dict-item]
        "sports_state_get_game_state",
        {"game_ref": "exact-ref"},
    )

    assert result is sentinel
    assert len(invocations) == 1
    assert invocations[0]["type"] == "tool_call"
    assert invocations[0]["name"] == "sports_state_get_game_state"
    assert invocations[0]["args"] == {"game_ref": "exact-ref"}
    assert invocations[0]["id"].startswith("watch-poll-")


@pytest.mark.asyncio
async def test_polling_batches_game_groups_through_one_mcp_session(monkeypatch) -> None:
    opened: list[frozenset[str]] = []

    @asynccontextmanager
    async def connect(servers):
        opened.append(servers)
        yield []

    provider = MCPWatchEvidenceProvider(connect)
    _, _, observations = replay()

    async def observe_with_tools(current_rule, now, tools):
        return observations[0].model_copy(update={"game_ref": current_rule.game.game_ref})

    monkeypatch.setattr(provider, "_observe_with_tools", observe_with_tools)
    assert await provider.observe_many([], BASE) == []
    assert opened == []
    await provider.observe_many(
        [rule(), rule().model_copy(update={"markets": rule().markets[:1]})], BASE
    )
    assert opened == [frozenset({"sports_state", "kalshi", "polymarket"})]


def test_preview_requires_confirmation_and_enforces_session_scope(tmp_path: Path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    service = WatchService(repository)
    game, markets, _ = replay()
    preview = service.preview(
        session_id="session-a",
        creation_source="cli",
        game=game,
        markets=markets,
        conditions=[condition()],
        delivery=DeliveryPolicy(),
    )
    assert repository.list_rules("session-a") == []
    assert "8" in preview.text and "probability points" in preview.text
    assert "cooldown 300s" in preview.text
    assert "starts 15m before" in preview.text
    with pytest.raises(KeyError):
        service.confirm("session-b", preview.draft_id)
    replacement = service.preview(
        session_id="session-a",
        creation_source="cli",
        game=game,
        markets=markets,
        conditions=[condition()],
        delivery=DeliveryPolicy(),
    )
    with pytest.raises(KeyError):
        service.confirm("session-a", preview.draft_id)
    activated = service.confirm("session-a", replacement.draft_id, now=BASE)
    assert repository.get_rule(activated.watch_id, "session-a") == activated
    assert repository.get_rule(activated.watch_id, "session-b") is None
    assert service.pause("session-a", activated.watch_id)
    assert service.inspect("session-a", activated.watch_id).status == WatchStatus.PAUSED
    assert service.resume("session-a", activated.watch_id)
    repository.set_condition_state(activated.watch_id, "move_scoring_event", False, BASE)
    revision = service.preview(
        session_id="session-a",
        creation_source="cli",
        game=game,
        markets=markets,
        conditions=[condition(threshold="0.09")],
        delivery=DeliveryPolicy(),
        replaces_watch_id=activated.watch_id,
    )
    revised = service.confirm("session-a", revision.draft_id, now=BASE + timedelta(minutes=1))
    assert revised.watch_id == activated.watch_id
    assert revised.conditions[0].threshold == Decimal("0.09")
    assert repository.condition_state(revised.watch_id, "move_scoring_event") == (True, None)
    assert service.delete("session-a", activated.watch_id)


def test_exact_probability_point_boundary_and_scoring_correlation() -> None:
    _, _, observations = replay()
    result = evaluate_condition(condition(), observations[1], observations[:1])
    assert result.matched
    assert result.metric == Decimal("0.08")
    trigger = build_trigger(rule(), result, observations[1].retrieved_at)
    assert trigger.message.startswith("MLB WATCH ALERT: Boston Red Sox at New York Yankees\n")
    assert "Kalshi: 42% -> 50% (up 8 pts)" in trigger.message
    assert "moved fast (within 60s)" in trigger.message
    assert "SCORE: New York scores on a two-run double" in trigger.message
    assert "Score: Boston Red Sox 0, New York Yankees 2" in trigger.message
    assert "Your rule: 8+ pt move within 2 min, only when a scoring play happens." in (
        trigger.message
    )
    assert "not proof they caused the move" in trigger.message
    assert len(trigger.message) <= 1500
    assert trigger.message.endswith("Alert time: 12:01 AM UTC")
    assert trigger.correlated_events[0].play_id == "score-101"


def test_alert_lists_recent_plays_in_game_local_time_and_folds_warnings() -> None:
    _, _, observations = replay()
    untimed = "Provider supplies no authoritative timestamp for this quote observation."
    current = observations[1].model_copy(
        update={
            "quotes": [
                quote.model_copy(update={"warning": untimed}) for quote in observations[1].quotes
            ]
        }
    )
    result = evaluate_condition(condition(relationship="any"), current, observations[:1])
    padres = rule().model_copy(
        update={
            "game": rule().game.model_copy(
                update={"game_ref": encode_game_ref(REFERENCE_LA), "league": "mlb"}
            )
        }
    )
    plays = [
        ScoringPlay(
            play_id="old",
            play_time=current.retrieved_at - timedelta(minutes=30),
            description="Old at-bat outside the window.",
            scoring=False,
        ),
        ScoringPlay(
            play_id="single",
            play_time=current.retrieved_at - timedelta(seconds=40),
            description="Judge singled to right.",
            period_label="3rd Inning",
            home_score=0,
            away_score=0,
            scoring=False,
        ),
        ScoringPlay(
            play_id="score-101",
            play_time=datetime(2026, 9, 24, 0, 0, 45, tzinfo=UTC),
            description="Stanton doubled, Judge scored.",
            period_label="3rd Inning",
            home_score=2,
            away_score=0,
            scoring=True,
        ),
    ]
    trigger = build_trigger(padres, result, current.retrieved_at, recent_plays=plays)
    message = trigger.message
    assert "Plays in the last 3 min:" in message
    assert "- 5:00 PM, 3rd Inning: Judge singled to right." in message
    assert "- 5:00 PM, 3rd Inning: SCORE: Stanton doubled, Judge scored." in message
    assert "Old at-bat" not in message
    assert "Score: Boston Red Sox 0, New York Yankees 2 (3rd Inning)" in message
    assert message.count("did not report quote times") == 1
    assert "Kalshi and Polymarket did not report quote times" in message
    assert message.endswith("Alert time: 5:01 PM PDT")
    assert [play.play_id for play in trigger.recent_plays] == ["old", "single", "score-101"]
    no_scoring = current.model_copy(update={"new_scoring_plays": []})
    quiet_result = evaluate_condition(condition(relationship="any"), no_scoring, observations[:1])
    quiet = build_trigger(padres, quiet_result, current.retrieved_at, recent_plays=plays[:1])
    assert "No plays in the last 3 min. Last play:" in quiet.message


def test_recent_play_filter_drops_pitch_noise() -> None:
    def play(text: str, kind: str = "plate_appearance", scoring: bool = False) -> GamePlay:
        return GamePlay.model_validate(
            {
                "play_id": text,
                "sequence": 1,
                "event_kind": kind,
                "text": text,
                "scoring_play": scoring,
                "wallclock": BASE,
                "context": {"sport": "baseball", "half": "top"},
            }
        )

    assert _alert_worthy(play("Ohtani singled to right."))
    assert _alert_worthy(play("Machado homered to left.", scoring=True))
    assert not _alert_worthy(play("Pitch 3 : Ball 1"))
    assert not _alert_worthy(play("End of the 3rd inning"))
    assert not _alert_worthy(play("Nick Pivetta pitches to Mookie Betts", kind="pitch"))


def test_no_tracked_scoring_event_is_bounded_and_source_required() -> None:
    _, _, observations = replay()
    current = observations[1].model_copy(update={"new_scoring_plays": []})
    no_event = evaluate_condition(
        condition(threshold="0.08", relationship="no_tracked_scoring_event"),
        current,
        observations[:1],
    )
    assert no_event.matched
    unavailable = current.model_copy(update={"sports_status": SourceStatus.MISSING})
    blocked = evaluate_condition(
        condition(threshold="0.08", relationship="no_tracked_scoring_event"),
        unavailable,
        observations[:1],
    )
    assert not blocked.matched
    assert "not evaluated" in blocked.warnings[0]


def test_stale_out_of_order_and_duplicate_play_evidence_do_not_fire() -> None:
    _, _, observations = replay()
    stale_quote = observations[1].quotes[0].model_copy(update={"status": SourceStatus.STALE})
    current = observations[1].model_copy(update={"quotes": [stale_quote]})
    assert not evaluate_condition(condition(), current, observations[:1]).matched
    older = current.model_copy(
        update={
            "quotes": [
                stale_quote.model_copy(
                    update={
                        "status": SourceStatus.AVAILABLE,
                        "quote_time": BASE - timedelta(minutes=1),
                    }
                )
            ]
        }
    )
    assert not evaluate_condition(condition(), older, observations[:1]).matched


def test_divergence_lifecycle_and_rearm_metrics_are_deterministic() -> None:
    _, _, observations = replay()
    divergence = evaluate_condition(
        DivergenceCondition(condition_id="spread", threshold=Decimal("0.01")),
        observations[0],
        [],
    )
    assert divergence.matched and divergence.metric == Decimal("0.01")
    final = observations[1].model_copy(update={"lifecycle": "final"})
    lifecycle = evaluate_condition(
        LifecycleCondition(condition_id="final", to_states=frozenset({"final"})),
        final,
        observations[:1],
    )
    assert lifecycle.matched
    lifecycle_alert = build_trigger(rule(), lifecycle, final.retrieved_at)
    assert "Game status changed: live -> final." in lifecycle_alert.message
    assert "caused the move" not in lifecycle_alert.message
    unavailable_lifecycle = evaluate_condition(
        LifecycleCondition(condition_id="delayed", to_states=frozenset({"delayed"})),
        final.model_copy(update={"lifecycle": "delayed", "sports_status": SourceStatus.MISSING}),
        observations[:1],
    )
    assert not unavailable_lifecycle.matched
    below = observations[1].model_copy(
        update={
            "quotes": [
                quote.model_copy(update={"price": quote.price - Decimal("0.07")})
                for quote in observations[1].quotes
            ]
        }
    )
    rearm = evaluate_condition(condition(relationship="any"), below, observations[:1])
    assert not rearm.matched and rearm.metric == Decimal("0.01")


class SequenceEvidence:
    def __init__(self, observations: list[WatchObservation]) -> None:
        self.observations = observations
        self.calls = 0

    async def observe(self, _: WatchRule, __: datetime) -> WatchObservation:
        value = self.observations[min(self.calls, len(self.observations) - 1)]
        self.calls += 1
        return value


class FailingEvidence:
    async def observe(self, _: WatchRule, __: datetime) -> WatchObservation:
        raise ValueError("malformed provider result")


@pytest.mark.asyncio
async def test_coordinator_shares_observations_and_recovers_from_restart(tmp_path: Path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    first = rule(watch_id="watch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    second = rule(watch_id="watch_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    repository.save_rule(first, due_at=BASE)
    repository.save_rule(second, due_at=BASE)
    _, _, observations = replay()
    evidence = SequenceEvidence(observations)
    coordinator = WatchCoordinator(repository, evidence)
    initial = await coordinator.poll(owner="runner-1", now=BASE)
    assert initial.observation_groups == 1
    assert evidence.calls == 1
    # A separate coordinator instance reads the durable baseline and fires both watches.
    restarted = WatchCoordinator(repository, evidence)
    result = await restarted.poll(owner="runner-2", now=BASE + timedelta(minutes=1))
    assert result.created_triggers == 2
    assert evidence.calls == 2
    assert result.model_calls == result.tavily_calls == 0
    assert len(repository.list_triggers("session-a")) == 2
    await WatchCoordinator(repository, FailingEvidence()).poll(
        owner="outage", now=BASE + timedelta(minutes=2)
    )
    assert repository.condition_state(first.watch_id, "move_scoring_event")[0] is False
    assert repository.condition_state(second.watch_id, "move_scoring_event")[0] is False


@pytest.mark.asyncio
async def test_coordinator_records_source_outage_without_triggering(tmp_path: Path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    repository.save_rule(rule(), due_at=BASE)
    _, _, observations = replay()
    await WatchCoordinator(repository, SequenceEvidence(observations[:1])).poll(
        owner="baseline", now=BASE
    )
    result = await WatchCoordinator(repository, FailingEvidence()).poll(
        owner="outage", now=BASE + timedelta(minutes=1)
    )
    assert result.source_warnings == 2
    assert result.created_triggers == 0
    stored = repository.recent_observations(
        "watch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", BASE - timedelta(minutes=1)
    )
    assert stored[-1].sports_status == SourceStatus.MISSING
    recovered = await WatchCoordinator(repository, SequenceEvidence(observations[:1])).poll(
        owner="recovered", now=BASE + timedelta(minutes=2)
    )
    assert recovered.claimed_watches == 1


@pytest.mark.asyncio
async def test_coordinator_performs_one_final_poll_then_stops_terminal_watch(
    tmp_path: Path,
) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    watched = rule()
    repository.save_rule(watched, due_at=BASE)
    _, _, observations = replay()
    final = observations[1].model_copy(
        update={
            "lifecycle": "final",
            "quotes": [
                quote.model_copy(update={"contract_terminal": True})
                for quote in observations[1].quotes
            ],
        }
    )
    coordinator = WatchCoordinator(repository, SequenceEvidence([final, final]))
    await coordinator.poll(owner="one", now=BASE)
    assert repository.get_rule(watched.watch_id, watched.session_id).status == WatchStatus.ACTIVE
    await coordinator.poll(owner="two", now=BASE + timedelta(minutes=15))
    assert repository.get_rule(watched.watch_id, watched.session_id).status == WatchStatus.TERMINAL


def test_sqlite_trigger_and_outbox_are_atomic_and_idempotent(tmp_path: Path) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    watched = rule(telegram=True)
    repository.save_rule(watched, due_at=BASE)
    _, _, observations = replay()
    result = evaluate_condition(condition(), observations[1], observations[:1])
    trigger = build_trigger(watched, result, BASE)
    assert repository.record_trigger(watched, trigger)
    assert not repository.record_trigger(watched, trigger)
    item = repository.outbox_for_trigger(trigger.trigger_id)
    assert item is not None and item.status == OutboxStatus.PENDING
    assert len(repository.claim_outbox("one", BASE)) == 1
    assert repository.claim_outbox("two", BASE) == []


class FakeTransaction:
    def __init__(self, data: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.data = data
        self.wrote = False

    def get(self, collection: str, document: str) -> dict[str, Any] | None:
        if self.wrote:
            raise AssertionError("Firestore transactions must complete reads before writes")
        value = self.data.get(collection, {}).get(document)
        return dict(value) if value else None

    def set(self, collection: str, document: str, data: dict[str, Any]) -> None:
        self.wrote = True
        self.data.setdefault(collection, {})[document] = dict(data)

    def delete(self, collection: str, document: str) -> None:
        self.wrote = True
        self.data.get(collection, {}).pop(document, None)

    def query(self, collection: str, **filters: Any):
        raise AssertionError("transaction scan not expected")


class FakeFirestore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    def atomic(self, operation):
        working = deepcopy(self.data)
        result = operation(FakeTransaction(working))
        self.data = working
        return result

    def query(self, collection: str, **filters: Any):
        limit = filters.pop("limit", 100)
        order_ascending = filters.pop("order_by_ascending", None)
        order_descending = filters.pop("order_by_descending", None)
        found = []
        for key, value in self.data.get(collection, {}).items():
            matches = True
            for field, expected in filters.items():
                actual_field = field.removesuffix("_lte").removesuffix("_gte")
                if actual_field == "due_at" and collection == "watch_outbox":
                    actual_field = "available_at"
                actual = value.get(actual_field)
                if field.endswith("_lte"):
                    matches &= actual <= expected
                elif field.endswith("_gte"):
                    matches &= actual >= expected
                else:
                    matches &= actual == expected
            if matches:
                found.append((key, dict(value)))
        if order_ascending:
            found.sort(key=lambda item: item[1][order_ascending])
        elif order_descending:
            found.sort(key=lambda item: item[1][order_descending], reverse=True)
        return found[:limit]


def test_firestore_fake_uses_atomic_rule_trigger_and_outbox_contract() -> None:
    store = FakeFirestore()
    repository = FirestoreWatchRepository(store)
    watched = rule(telegram=True)
    repository.save_rule(watched, due_at=BASE)
    assert repository.get_rule(watched.watch_id, "session-a") == watched
    _, _, observations = replay()
    revised = watched.model_copy(
        update={
            "confirmed_at": BASE,
            "conditions": [
                condition(),
                LifecycleCondition(condition_id="final", to_states=frozenset({"final"})),
            ],
        }
    )
    repository.replace_rule(revised, "session-a")
    assert repository.get_rule(watched.watch_id, "session-a") == revised
    assert repository.condition_state(watched.watch_id, "move_scoring_event") == (True, None)
    assert repository.condition_state(watched.watch_id, "final") == (True, None)
    repository.add_observation(watched.watch_id, observations[1])
    trigger = build_trigger(
        revised, evaluate_condition(condition(), observations[1], observations[:1]), BASE
    )
    assert repository.record_trigger(revised, trigger)
    assert not repository.record_trigger(revised, trigger)
    outbox = repository.outbox_for_trigger(trigger.trigger_id)
    assert outbox is not None
    claimed = repository.claim_outbox("delivery", BASE)
    assert claimed == [
        outbox.model_copy(
            update={
                "status": OutboxStatus.LEASED,
                "attempts": 1,
                "lease_owner": "delivery",
                "lease_until": BASE + timedelta(seconds=30),
            }
        )
    ]
    assert repository.finish_outbox(
        outbox.outbox_id,
        "delivery",
        OutboxStatus.SENT,
        available_at=BASE,
        provider_message_id="123",
    )
    assert repository.claim_outbox("again", BASE + timedelta(minutes=1)) == []
    assert repository.cleanup(BASE + timedelta(days=31)) == (0, 1)
    assert repository.trigger_by_id(trigger.trigger_id) is None
    assert repository.delete_rule(watched.watch_id, "session-a")
    assert repository.get_rule(watched.watch_id, "session-a") is None
    assert all(
        data.get("watch_id") != watched.watch_id
        for collection in store.data.values()
        for data in collection.values()
    )


class FakeTransport:
    def __init__(self, response: TransportResponse | Exception) -> None:
        self.response = response
        self.calls = 0

    async def send(self, token: SecretStr, chat_id: SecretStr, text: str) -> TransportResponse:
        self.calls += 1
        assert token.get_secret_value() == "test-token"
        assert chat_id.get_secret_value() == "test-chat"
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "classification"),
    [
        (TransportResponse(429, {"parameters": {"retry_after": 7}}), "rate_limited"),
        (TransportResponse(500, {}), "server_error"),
        (TransportResponse(200, {"ok": True}), "malformed_response"),
    ],
)
async def test_telegram_transient_failures_are_sanitized(response, classification) -> None:
    delivery = TelegramDelivery(
        SecretStr("test-token"), SecretStr("test-chat"), FakeTransport(response)
    )
    _, _, observations = replay()
    trigger = build_trigger(
        rule(), evaluate_condition(condition(), observations[1], observations[:1]), BASE
    )
    with pytest.raises(TelegramTransientError) as caught:
        await delivery.deliver(trigger)
    assert caught.value.classification == classification
    assert "test-token" not in str(caught.value)
    assert "test-chat" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("classification", ["ReadTimeout", "RemoteProtocolError"])
async def test_telegram_timeout_and_disconnect_schedule_bounded_retry(
    tmp_path: Path, classification: str
) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    watched = rule(telegram=True)
    repository.save_rule(watched, due_at=BASE)
    _, _, observations = replay()
    trigger = build_trigger(
        watched, evaluate_condition(condition(), observations[1], observations[:1]), BASE
    )
    repository.record_trigger(watched, trigger)
    transport = FakeTransport(TelegramTransientError(classification))
    delivery = TelegramDelivery(SecretStr("test-token"), SecretStr("test-chat"), transport)
    assert await DeliveryWorker(repository, delivery).run_once("delivery", BASE) == 1
    item = repository.outbox_for_trigger(trigger.trigger_id)
    assert item is not None
    assert item.status == OutboxStatus.RETRY
    assert BASE < item.available_at <= BASE + timedelta(minutes=5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        TransportResponse(401, {"description": "Unauthorized"}),
        TransportResponse(403, {"description": "bot was blocked by the user"}),
    ],
)
async def test_telegram_terminal_failures_keep_inbox(tmp_path: Path, response) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    watched = rule(telegram=True)
    repository.save_rule(watched, due_at=BASE)
    _, _, observations = replay()
    trigger = build_trigger(
        watched, evaluate_condition(condition(), observations[1], observations[:1]), BASE
    )
    repository.record_trigger(watched, trigger)
    delivery = TelegramDelivery(
        SecretStr("test-token"), SecretStr("test-chat"), FakeTransport(response)
    )
    assert await DeliveryWorker(repository, delivery).run_once("delivery", BASE) == 1
    item = repository.outbox_for_trigger(trigger.trigger_id)
    assert item is not None and item.status == OutboxStatus.FAILED
    assert repository.list_triggers("session-a") == [trigger]


@pytest.mark.asyncio
async def test_missing_telegram_configuration_remains_bounded_and_inbox_first(
    tmp_path: Path,
) -> None:
    repository = SQLiteWatchRepository(tmp_path / "watches.db")
    watched = rule(telegram=True)
    repository.save_rule(watched, due_at=BASE)
    _, _, observations = replay()
    trigger = build_trigger(
        watched, evaluate_condition(condition(), observations[1], observations[:1]), BASE
    )
    repository.record_trigger(watched, trigger)
    worker = DeliveryWorker(repository, None)
    for attempt in range(25):
        assert (
            await worker.run_once(f"delivery-{attempt}", BASE + timedelta(minutes=15 * attempt))
            == 1
        )
    item = repository.outbox_for_trigger(trigger.trigger_id)
    assert item is not None
    assert item.status == OutboxStatus.RETRY
    assert item.attempts == 20
    assert repository.list_triggers("session-a") == [trigger]


def test_oidc_and_secret_manager_boundaries_use_controlled_substitutes() -> None:
    claims = {
        "aud": "https://service.example",
        "email": "scheduler@example.iam.gserviceaccount.com",
        "email_verified": True,
        "iss": "https://accounts.google.com",
    }
    verifier = GoogleOIDCVerifier(
        claims["aud"], claims["email"], verify=lambda token, audience: claims
    )
    assert verifier.verify("Bearer controlled-token")
    assert not verifier.verify(None)

    class SecretClient:
        def access_secret_version(self, request):
            assert request["name"].endswith("/secrets/telegram-token/versions/latest")
            payload = type("Payload", (), {"data": b"secret-value"})()
            return type("Response", (), {"payload": payload})()

    assert GoogleSecretLoader("project", SecretClient()).access("telegram-token") == "secret-value"
    with pytest.raises(ValueError):
        GoogleSecretLoader("project", SecretClient()).access("projects/x/secrets/y")
