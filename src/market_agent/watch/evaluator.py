"""Deterministic watch evaluation and bounded evidence messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from market_agent.providers.game_state import decode_game_ref
from market_agent.watch.models import (
    DivergenceCondition,
    EvidenceDelta,
    GameIdentity,
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


_PLATFORM_NAMES = {"kalshi": "Kalshi", "polymarket": "Polymarket"}
_MISSING_QUOTE_TIME = "no authoritative timestamp"
_MAX_PLAYS = 5


def _platform(name: str) -> str:
    return _PLATFORM_NAMES.get(name, name.replace("_", " ").title())


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "?"
    points = (value * 100).quantize(Decimal("0.1"))
    return f"{points:.0f}%" if points == points.to_integral_value() else f"{points}%"


def _points(value: Decimal) -> str:
    points = abs(value * 100).quantize(Decimal("0.1"))
    return f"{points:.0f}" if points == points.to_integral_value() else str(points)


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _duration(seconds: int) -> str:
    return f"{seconds}s" if seconds < 90 else f"{round(seconds / 60)} min"


def _display_zone(game: GameIdentity) -> tzinfo:
    try:
        return ZoneInfo(decode_game_ref(game.game_ref).timezone)
    except Exception:
        return UTC


def _clock(value: datetime, zone: tzinfo) -> str:
    local = value.astimezone(zone)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _rule_summary(condition: WatchCondition) -> str:
    if isinstance(condition, PriceMoveCondition):
        cause = {
            "any": "any cause",
            "scoring_event": "only when a scoring play happens",
            "no_tracked_scoring_event": "only when no scoring play happens",
        }[condition.event_relationship]
        return (
            f"Your rule: {_points(condition.threshold)}+ pt move within "
            f"{_duration(condition.window_seconds)}, {cause}."
        )
    if isinstance(condition, DivergenceCondition):
        return f"Your rule: Kalshi and Polymarket {_points(condition.threshold)}+ pts apart."
    return "Your rule: game status changes to " + ", ".join(sorted(condition.to_states)) + "."


def _play_window(condition: WatchCondition) -> int:
    if isinstance(condition, PriceMoveCondition):
        # Markets react after the play, and polling adds up to a minute of lag.
        return max(condition.window_seconds, condition.correlation_window_seconds) + 60
    return 300


def _play_line(play: ScoringPlay, zone: tzinfo) -> str:
    when = _clock(play.play_time, zone)
    if play.period_label:
        when += f", {play.period_label}"
    marker = "SCORE: " if play.scoring else ""
    return f"- {when}: {marker}{_short(play.description, 110)}"


def _score_line(game: GameIdentity, plays: list[ScoringPlay]) -> str | None:
    latest = next(
        (
            play
            for play in reversed(plays)
            if play.home_score is not None and play.away_score is not None
        ),
        None,
    )
    if latest is None:
        return None
    line = (
        f"Score: {_short(game.away_team, 40)} {latest.away_score}, "
        f"{_short(game.home_team, 40)} {latest.home_score}"
    )
    return line + (f" ({latest.period_label})" if latest.period_label else "")


def _notes(warnings: tuple[str, ...]) -> list[str]:
    untimed = sorted(
        {
            _platform(warning.split(":", 1)[0])
            for warning in warnings
            if _MISSING_QUOTE_TIME in warning
        }
    )
    notes = []
    if untimed:
        notes.append(
            f"Note: {' and '.join(untimed)} did not report quote times, so fetch times were used."
        )
    notes.extend(
        f"Note: {_short(warning, 140)}"
        for warning in warnings
        if _MISSING_QUOTE_TIME not in warning
    )
    return notes[:3]


def _headline(result: ConditionResult, prices: list[EvidenceDelta]) -> list[str]:
    condition = result.condition
    if isinstance(condition, PriceMoveCondition) and prices:
        longest = max(delta.window_seconds or 0 for delta in prices)
        lines = [
            f"{_short(prices[0].outcome, 60)} win chance moved fast (within {_duration(longest)}).",
            "",
        ]
        for delta in prices:
            assert delta.before_price is not None and delta.after_price is not None
            change = delta.after_price - delta.before_price
            lines.append(
                f"{_platform(delta.platform)}: {_percent(delta.before_price)} -> "
                f"{_percent(delta.after_price)} ({'up' if change > 0 else 'down'} "
                f"{_points(change)} pts)"
            )
        return lines
    if isinstance(condition, DivergenceCondition) and prices:
        spread = result.metric if result.metric is not None else Decimal(0)
        return [
            f"Kalshi and Polymarket disagree by {_points(spread)} pts on "
            f"{_short(prices[0].outcome, 60)}.",
            "",
            *(f"{_platform(delta.platform)}: {_percent(delta.after_price)}" for delta in prices),
        ]
    return [f"Game status changed: {result.lifecycle_before} -> {result.lifecycle_after}."]


def _play_section(
    game: GameIdentity,
    result: ConditionResult,
    triggered_at: datetime,
    recent_plays: list[ScoringPlay],
    zone: tzinfo,
) -> list[str]:
    known = {play.play_id: play for play in recent_plays}
    for event in result.events:
        # Prefer the recent-feed copy: it carries the period label.
        known[event.play_id] = known.get(event.play_id, event).model_copy(update={"scoring": True})
    ordered = sorted(
        (play for play in known.values() if play.play_time <= triggered_at),
        key=lambda play: (play.play_time, play.play_id),
    )
    window = _play_window(result.condition)
    since = triggered_at - timedelta(seconds=window)
    nearby = [play for play in ordered if play.play_time >= since][-_MAX_PLAYS:]
    if nearby:
        lines = [f"Plays in the last {_duration(window)}:"]
        lines.extend(_play_line(play, zone) for play in nearby)
    elif ordered:
        lines = [
            f"No plays in the last {_duration(window)}. Last play:",
            _play_line(ordered[-1], zone),
        ]
    else:
        lines = ["No play-by-play available for this window."]
    score = _score_line(game, ordered)
    return [*lines, score] if score else lines


def render_alert(
    rule: WatchRule,
    result: ConditionResult,
    triggered_at: datetime,
    recent_plays: list[ScoringPlay] | None = None,
) -> str:
    """Plain-language alert: what moved, what happened on the field, and the rule that fired."""
    zone = _display_zone(rule.game)
    prices = [delta for delta in result.deltas if delta.platform != "sports_state"]
    lines = [
        f"{rule.game.league.upper()} WATCH ALERT: {_short(rule.game.label, 170)}",
        *_headline(result, prices),
        "",
        *_play_section(rule.game, result, triggered_at, recent_plays or [], zone),
        "",
        _rule_summary(result.condition),
        *_notes(result.warnings),
    ]
    footer = f"Alert time: {_clock(triggered_at, zone)} {triggered_at.astimezone(zone).tzname()}"
    if prices:
        footer = f"Plays are timing context, not proof they caused the move.\n{footer}"
    available = 1500 - len(footer) - 1
    body = "\n".join(lines)
    if len(body) > available:
        body = body[: available - 3].rstrip() + "..."
    return f"{body}\n{footer}"


def build_trigger(
    rule: WatchRule,
    result: ConditionResult,
    triggered_at: datetime,
    recent_plays: list[ScoringPlay] | None = None,
) -> WatchTrigger:
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
        recent_plays=list(recent_plays or [])[-10:],
        lifecycle_before=result.lifecycle_before,
        lifecycle_after=result.lifecycle_after,
        source_warnings=list(result.warnings),
        observation_ids=list(result.evidence_ids),
        message=render_alert(rule, result, triggered_at, recent_plays),
    )
