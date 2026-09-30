"""Deterministic readiness, state transitions, and lifecycle messages."""

from __future__ import annotations

import hashlib
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

from market_agent.providers.game_state import SportsStateError, decode_game_ref
from market_agent.watch.models import (
    DivergenceCondition,
    LifecycleCondition,
    LifecycleEventKind,
    PriceMoveCondition,
    SourceReadiness,
    SourceStatus,
    WatchLifecycleEvent,
    WatchObservation,
    WatchRule,
    WatchRuntimeState,
    WatchRuntimeSummary,
)


def condition_summary(rule: WatchRule) -> str:
    parts: list[str] = []
    for condition in rule.conditions:
        if isinstance(condition, PriceMoveCondition):
            parts.append(
                f"{condition.threshold * 100:g} point move within {condition.window_seconds}s"
            )
        elif isinstance(condition, DivergenceCondition):
            parts.append(f"{condition.threshold * 100:g} point platform gap")
        else:
            parts.append("game becomes " + ", ".join(sorted(condition.to_states)))
    return "; ".join(parts)


def source_readiness(
    rule: WatchRule, observation: WatchObservation
) -> tuple[list[SourceReadiness], int]:
    """Return typed source coverage and the number of evaluable conditions."""
    quotes = {(q.platform, q.market_id, q.outcome.casefold()): q for q in observation.quotes}
    entries: list[SourceReadiness] = []
    usable_platforms: set[str] = set()
    usable_quotes = 0
    for market in rule.markets:
        quote = quotes.get((market.platform, market.market_id, market.outcome.casefold()))
        good = bool(
            quote
            and quote.status == SourceStatus.AVAILABLE
            and quote.price is not None
            and not quote.contract_terminal
        )
        if good:
            usable_quotes += 1
            usable_platforms.add(market.platform)
        entries.append(
            SourceReadiness(
                source=f"{market.platform}:{market.market_id}",
                status=quote.status if quote else SourceStatus.MISSING,
                usable=good,
                warning=None if good else "Market price unavailable, stale, or terminal.",
            )
        )
    state_status = observation.state_status or observation.sports_status
    play_status = observation.play_status or observation.sports_status
    state_good = state_status == SourceStatus.AVAILABLE
    plays_good = play_status == SourceStatus.AVAILABLE
    entries.extend(
        [
            SourceReadiness(
                source="sports_state",
                status=state_status,
                usable=state_good,
                warning=None if state_good else "Game state unavailable.",
            ),
            SourceReadiness(
                source="scoring_plays",
                status=play_status,
                usable=plays_good,
                warning=None if plays_good else "Scoring plays unavailable.",
            ),
        ]
    )
    usable_conditions = sum(
        (usable_quotes > 0 and (c.event_relationship == "any" or (state_good and plays_good)))
        if isinstance(c, PriceMoveCondition)
        else (len(usable_platforms) == 2 if isinstance(c, DivergenceCondition) else state_good)
        for c in rule.conditions
    )
    return entries, usable_conditions


def target_state(
    rule: WatchRule, observation: WatchObservation
) -> tuple[WatchRuntimeState, list[SourceReadiness]]:
    sources, usable = source_readiness(rule, observation)
    if usable == 0:
        return WatchRuntimeState.AWAITING_SOURCES, sources
    need_state = any(
        isinstance(c, LifecycleCondition)
        or (isinstance(c, PriceMoveCondition) and c.event_relationship != "any")
        for c in rule.conditions
    )
    need_plays = any(
        isinstance(c, PriceMoveCondition) and c.event_relationship != "any" for c in rule.conditions
    )
    required = [
        s
        for s in sources
        if s.source not in {"sports_state", "scoring_plays"}
        or (s.source == "sports_state" and need_state)
        or (s.source == "scoring_plays" and need_plays)
    ]
    if usable < len(rule.conditions) or any(not source.usable for source in required):
        return WatchRuntimeState.DEGRADED, sources
    return WatchRuntimeState.MONITORING, sources


def transition_kind(
    before: WatchRuntimeSummary, after: WatchRuntimeState, *, terminal: bool = False
) -> LifecycleEventKind | None:
    if terminal:
        return (
            LifecycleEventKind.COMPLETED
            if before.runtime_state != WatchRuntimeState.TERMINAL
            else None
        )
    if before.runtime_state == WatchRuntimeState.AWAITING_FIRST_POLL or (
        before.runtime_state == WatchRuntimeState.AWAITING_SOURCES
        and before.first_success_at is None
        and after != WatchRuntimeState.AWAITING_SOURCES
    ):
        if after == WatchRuntimeState.AWAITING_SOURCES:
            return LifecycleEventKind.WAITING
        if before.activation_kind == "resumed":
            return LifecycleEventKind.RESUMED
        if before.activation_kind == "updated":
            return LifecycleEventKind.UPDATED
        return (
            LifecycleEventKind.STARTED
            if after == WatchRuntimeState.MONITORING
            else LifecycleEventKind.STARTED_DEGRADED
        )
    if before.runtime_state == after:
        return None
    if after == WatchRuntimeState.AWAITING_SOURCES:
        return LifecycleEventKind.INTERRUPTED
    if before.runtime_state in {WatchRuntimeState.AWAITING_SOURCES, WatchRuntimeState.DEGRADED}:
        return LifecycleEventKind.RECOVERED
    if after == WatchRuntimeState.DEGRADED:
        return LifecycleEventKind.DEGRADED
    return None


def make_event(
    rule: WatchRule,
    runtime: WatchRuntimeSummary,
    kind: LifecycleEventKind,
    observation: WatchObservation,
    now: datetime,
    origin: str,
) -> WatchLifecycleEvent:
    fingerprint = hashlib.sha256(
        f"{rule.watch_id}:{runtime.activation_epoch}:{runtime.transition_sequence}:"
        f"{kind.value}:{observation.observation_id}".encode()
    ).hexdigest()
    try:
        game_timezone: tzinfo = ZoneInfo(decode_game_ref(rule.game.game_ref).timezone)
    except SportsStateError:
        game_timezone = rule.game.scheduled_start.tzinfo or ZoneInfo("UTC")
    local_time = now.astimezone(game_timezone).isoformat()
    cadence = 60 if observation.lifecycle in {"live", "halftime"} else 300
    if observation.lifecycle in {"final", "cancelled"}:
        cadence = 900
    missing = ", ".join(s.source for s in runtime.source_readiness if not s.usable) or "none"
    contracts = ", ".join(f"{m.platform} {m.outcome}" for m in rule.markets)
    message = (
        f"WATCH {kind.value.replace('_', ' ').upper()}: {rule.game.label}\n"
        f"Watch: {rule.watch_id}\nContracts: {contracts}\n"
        f"Rule: {runtime.condition_summary}\nState: {runtime.runtime_state.value}\n"
        f"Missing coverage: {missing}\nChecked: {local_time}\n"
        f"Poll cadence: {cadence}s; source: {origin}.\n"
        + (
            "No usable condition yet; checking again."
            if runtime.runtime_state == WatchRuntimeState.AWAITING_SOURCES
            else "Monitoring stops after the terminal game and contracts."
            if runtime.runtime_state == WatchRuntimeState.TERMINAL
            else "Monitoring continues on the next due check."
        )
    )[:1500]
    return WatchLifecycleEvent(
        event_id=f"event_{fingerprint[:32]}",
        kind=kind,
        watch_id=rule.watch_id,
        session_id=rule.session_id,
        activation_epoch=runtime.activation_epoch,
        fingerprint=fingerprint,
        game=rule.game,
        markets=rule.markets,
        condition_summary=runtime.condition_summary,
        cadence_seconds=cadence,
        runtime_state=runtime.runtime_state,
        source_readiness=runtime.source_readiness,
        observation_ids=[observation.observation_id],
        occurred_at=now,
        game_local_time=local_time,
        message=message,
    )
