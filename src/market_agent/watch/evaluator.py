"""Deterministic watch evaluation and bounded evidence messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from market_agent.watch.models import (
    DivergenceCondition,
    EvidenceDelta,
    LifecycleCondition,
    PriceMoveCondition,
    QuoteObservation,
    ScoringPlay,
    SourceStatus,
    WatchCondition,
    WatchObservation,
    WatchRule,
    WatchTrigger,
)


@dataclass(frozen=True)
class ConditionResult:
    condition: WatchCondition
    matched: bool
    deltas: tuple[EvidenceDelta, ...] = ()
    events: tuple[ScoringPlay, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    lifecycle_before: str | None = None
    lifecycle_after: str | None = None
    metric: Decimal | None = None


def _quote_key(quote: QuoteObservation) -> tuple[str, str, str]:
    return quote.platform, quote.market_id, quote.outcome.casefold()


def _usable(quote: QuoteObservation) -> bool:
    return quote.status == SourceStatus.AVAILABLE and quote.price is not None


def _quote_clock(quote: QuoteObservation) -> datetime:
    return quote.quote_time or quote.retrieved_at


def _scoring_plays(
    observations: list[WatchObservation], start: datetime, end: datetime
) -> list[ScoringPlay]:
    found: dict[str, ScoringPlay] = {}
    for observation in observations:
        for play in observation.new_scoring_plays:
            if start <= play.play_time <= end:
                found[play.play_id] = play
    return sorted(found.values(), key=lambda play: (play.play_time, play.play_id))


def _price_move(
    condition: PriceMoveCondition,
    current: WatchObservation,
    history: list[WatchObservation],
) -> ConditionResult:
    warnings: list[str] = []
    deltas: list[EvidenceDelta] = []
    events: dict[str, ScoringPlay] = {}
    evidence = {current.observation_id}
    observed_movements: list[Decimal] = []
    all_observations = [*history, current]
    if condition.event_relationship != "any" and current.sports_status != SourceStatus.AVAILABLE:
        return ConditionResult(
            condition,
            False,
            warnings=("Sports source unavailable; event-dependent trigger was not evaluated.",),
        )
    for after in current.quotes:
        if after.warning:
            warnings.append(f"{after.platform}: {after.warning}")
        if not _usable(after):
            warnings.append(
                f"{after.platform} quote unavailable or stale; its price trigger was not evaluated."
            )
            continue
        after_time = _quote_clock(after)
        earliest = after_time - timedelta(seconds=condition.window_seconds)
        candidates: list[tuple[WatchObservation, QuoteObservation]] = []
        for observation in history:
            for before in observation.quotes:
                before_time = _quote_clock(before)
                if (
                    _quote_key(before) == _quote_key(after)
                    and _usable(before)
                    and earliest <= before_time < after_time
                ):
                    candidates.append((observation, before))
        if not candidates:
            continue
        before_observation, before = min(candidates, key=lambda item: _quote_clock(item[1]))
        assert before.price is not None and after.price is not None
        movement = abs(after.price - before.price)
        observed_movements.append(movement)
        if movement < condition.threshold:
            continue
        correlation_start = after_time - timedelta(seconds=condition.correlation_window_seconds)
        correlated = _scoring_plays(all_observations, correlation_start, after_time)
        if condition.event_relationship == "scoring_event" and not correlated:
            continue
        if condition.event_relationship == "no_tracked_scoring_event" and correlated:
            continue
        for play in correlated:
            events[play.play_id] = play
        evidence.add(before_observation.observation_id)
        deltas.append(
            EvidenceDelta(
                platform=after.platform,
                market_id=after.market_id,
                outcome=after.outcome,
                before_price=before.price,
                after_price=after.price,
                window_seconds=round((after_time - _quote_clock(before)).total_seconds()),
            )
        )
    metric = max(observed_movements, default=None)
    return ConditionResult(
        condition=condition,
        matched=bool(deltas),
        deltas=tuple(deltas),
        events=tuple(events.values()),
        warnings=tuple(sorted(set(warnings))),
        evidence_ids=tuple(sorted(evidence)),
        metric=metric,
    )


def _divergence(condition: DivergenceCondition, current: WatchObservation) -> ConditionResult:
    usable = [quote for quote in current.quotes if _usable(quote)]
    platforms = {quote.platform for quote in usable}
    if len(platforms) < 2:
        return ConditionResult(
            condition,
            False,
            warnings=("A required market source is unavailable; divergence was not evaluated.",),
        )
    prices = [quote.price for quote in usable if quote.price is not None]
    divergence = max(prices) - min(prices)
    deltas = tuple(
        EvidenceDelta(
            platform=quote.platform,
            market_id=quote.market_id,
            outcome=quote.outcome,
            before_price=min(prices),
            after_price=quote.price,
        )
        for quote in usable
    )
    return ConditionResult(
        condition,
        divergence >= condition.threshold,
        deltas=deltas,
        evidence_ids=(current.observation_id,),
        metric=divergence,
    )


def _lifecycle(
    condition: LifecycleCondition,
    current: WatchObservation,
    history: list[WatchObservation],
) -> ConditionResult:
    if current.sports_status != SourceStatus.AVAILABLE:
        return ConditionResult(
            condition,
            False,
            warnings=("Sports state unavailable; lifecycle trigger was not evaluated.",),
        )
    previous = history[-1] if history else None
    changed = previous is not None and previous.lifecycle != current.lifecycle
    matched = changed and current.lifecycle in condition.to_states
    return ConditionResult(
        condition,
        matched,
        deltas=(
            EvidenceDelta(
                platform="sports_state",
                market_id=current.game_ref,
                outcome="lifecycle",
                before_price=None,
                after_price=None,
            ),
        )
        if matched
        else (),
        evidence_ids=(previous.observation_id, current.observation_id) if previous else (),
        lifecycle_before=previous.lifecycle if previous else None,
        lifecycle_after=current.lifecycle,
    )


def evaluate_condition(
    condition: WatchCondition,
    current: WatchObservation,
    history: list[WatchObservation],
) -> ConditionResult:
    if isinstance(condition, PriceMoveCondition):
        return _price_move(condition, current, history)
    if isinstance(condition, DivergenceCondition):
        return _divergence(condition, current)
    return _lifecycle(condition, current, history)


def _fingerprint(rule: WatchRule, result: ConditionResult) -> str:
    material = {
        "watch_id": rule.watch_id,
        "condition_id": result.condition.condition_id,
        "evidence": result.evidence_ids,
        "deltas": [delta.model_dump(mode="json") for delta in result.deltas],
        "events": [event.play_id for event in result.events],
        "lifecycle": [result.lifecycle_before, result.lifecycle_after],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _points(value: Decimal | None) -> str:
    return "unknown" if value is None else f"{value * 100:.1f}"


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def render_alert(rule: WatchRule, result: ConditionResult, triggered_at: datetime) -> str:
    game_label = f"{_short(rule.game.away_team, 80)} at {_short(rule.game.home_team, 80)}"
    lines = [
        f"WATCH ALERT - {game_label}",
        f"Game identity: {rule.game.league} | {rule.game.scheduled_start.isoformat()}",
    ]
    priced_contracts: set[tuple[str, str, str]] = set()
    for delta in result.deltas:
        if delta.platform == "sports_state":
            continue
        priced_contracts.add((delta.platform, delta.market_id, delta.outcome.casefold()))
        lines.append(
            f"{delta.platform.title()} | {_short(delta.outcome, 50)} | {delta.market_id}: "
            f"{_points(delta.before_price)}% -> {_points(delta.after_price)}%"
        )
        if delta.window_seconds is not None:
            configured_window = (
                result.condition.window_seconds
                if isinstance(result.condition, PriceMoveCondition)
                else delta.window_seconds
            )
            lines.append(
                f"Evaluation window: observed {delta.window_seconds}s within configured "
                f"{configured_window}s"
            )
    for market in rule.markets:
        if (market.platform, market.market_id, market.outcome.casefold()) not in priced_contracts:
            lines.append(
                f"{market.platform.title()} | {_short(market.outcome, 50)} | "
                f"{market.market_id}: "
                "before/after price not applicable"
            )
    if result.events:
        lines.append(
            "Correlated scoring: "
            + "; ".join(_short(event.description, 80) for event in result.events[:2])
            + (f"; +{len(result.events) - 2} more" if len(result.events) > 2 else "")
        )
    elif (
        isinstance(result.condition, PriceMoveCondition)
        and result.condition.event_relationship == "no_tracked_scoring_event"
    ):
        lines.append(
            "Event context: no new normalized scoring play appears in the configured "
            f"{result.condition.correlation_window_seconds}s correlation window."
        )
    elif isinstance(result.condition, PriceMoveCondition):
        lines.append("Event context: price-only rule; no event relationship was configured.")
    if result.lifecycle_before != result.lifecycle_after:
        lines.append(f"Lifecycle: {result.lifecycle_before} -> {result.lifecycle_after}")
    lines.extend(f"Source warning: {_short(warning, 160)}" for warning in result.warnings[:3])
    if not result.warnings:
        freshness = (
            "not used for this lifecycle trigger"
            if isinstance(result.condition, LifecycleCondition)
            else "evaluated quotes passed source freshness checks"
        )
        lines.append(f"Quote freshness: {freshness}.")
    footer = (
        f"Triggered: {triggered_at.isoformat()}\n"
        "Timing alignment is correlation, not proof of causation."
    )
    available = 1500 - len(footer) - 1
    body = "\n".join(lines)
    if len(body) > available:
        body = body[: available - 3].rstrip() + "..."
    return f"{body}\n{footer}"


def build_trigger(rule: WatchRule, result: ConditionResult, triggered_at: datetime) -> WatchTrigger:
    return WatchTrigger(
        trigger_id=f"trigger_{uuid4().hex}",
        watch_id=rule.watch_id,
        session_id=rule.session_id,
        condition_id=result.condition.condition_id,
        fingerprint=_fingerprint(rule, result),
        triggered_at=triggered_at,
        game=rule.game,
        deltas=list(result.deltas),
        correlated_events=list(result.events),
        lifecycle_before=result.lifecycle_before,
        lifecycle_after=result.lifecycle_after,
        source_warnings=list(result.warnings),
        observation_ids=list(result.evidence_ids),
        message=render_alert(rule, result, triggered_at),
    )
