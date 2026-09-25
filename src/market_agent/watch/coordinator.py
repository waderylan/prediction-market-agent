"""No-model polling coordinator over the existing MCP evidence tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast, runtime_checkable
from uuid import uuid4

from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool

from market_agent.agent import _validate_tool_result, mcp_tools_for_servers
from market_agent.mcp.common import MarketDetail
from market_agent.providers.game_state import GamePlay, GameState, PlayByPlay
from market_agent.watch.evaluator import build_trigger, evaluate_condition
from market_agent.watch.models import (
    LifecycleCondition,
    PollResult,
    PriceMoveCondition,
    QuoteObservation,
    ScoringPlay,
    SourceStatus,
    WatchObservation,
    WatchRule,
    WatchStatus,
)
from market_agent.watch.repository import WatchRepository


class EvidenceProvider(Protocol):
    async def observe(self, rule: WatchRule, now: datetime) -> WatchObservation: ...


@runtime_checkable
class BatchEvidenceProvider(Protocol):
    async def observe_many(
        self, rules: list[WatchRule], now: datetime
    ) -> list[WatchObservation]: ...


ToolConnection = Callable[[frozenset[str]], AbstractAsyncContextManager[list[BaseTool]]]

# ESPN labels individual pitches and inning banners as plays; alerts only need outcomes.
_PLAY_NOISE = re.compile(r"^(pitch \d+\s*:|.* pitches to |(top|bottom|middle|end) of the )", re.I)


def _alert_worthy(play: GamePlay) -> bool:
    if play.wallclock is None or play.event_kind in {"pitch", "substitution"}:
        return False
    return play.scoring_play or not _PLAY_NOISE.match(play.text)


def _summarize(play: GamePlay) -> ScoringPlay:
    assert play.wallclock is not None
    return ScoringPlay(
        play_id=play.play_id,
        play_time=play.wallclock,
        description=play.text,
        home_score=play.home_score,
        away_score=play.away_score,
        period_label=play.period_label,
        scoring=play.scoring_play,
    )


class MCPWatchEvidenceProvider:
    """Uses exact pinned identities with sports-state and market MCP tools only."""

    def __init__(self, connect: ToolConnection = mcp_tools_for_servers) -> None:
        self.connect = connect

    @staticmethod
    def required_servers(rule: WatchRule) -> frozenset[str]:
        return frozenset({"sports_state", *(market.platform for market in rule.markets)})

    async def _invoke(
        self, tools: dict[str, BaseTool], name: str, arguments: dict[str, object]
    ) -> Any:
        tool = tools.get(name)
        if tool is None:
            raise ConnectionError(f"{name} unavailable")
        # MCP tools return their structured artifact only when invoked with a full
        # LangChain tool-call envelope. Raw arguments return display content alone,
        # which cannot pass the host application's schema validation.
        call = ToolCall(
            name=name,
            args=arguments,
            id=f"watch-poll-{uuid4().hex}",
            type="tool_call",
        )
        result = await tool.ainvoke(call)
        if not isinstance(result, ToolMessage) or result.status == "error":
            raise ValueError(f"{name} failed")
        return _validate_tool_result(name, arguments, result)

    async def observe(self, rule: WatchRule, now: datetime) -> WatchObservation:
        async with self.connect(self.required_servers(rule)) as discovered:
            tools = {tool.name: tool for tool in discovered}
            return await self._observe_with_tools(rule, now, tools)

    async def observe_many(self, rules: list[WatchRule], now: datetime) -> list[WatchObservation]:
        if not rules:
            return []
        servers = frozenset().union(*(self.required_servers(rule) for rule in rules))
        async with self.connect(servers) as discovered:
            tools = {tool.name: tool for tool in discovered}
            group_capacity = asyncio.Semaphore(2)

            async def observe_bounded(rule: WatchRule) -> WatchObservation:
                async with group_capacity:
                    return await self._observe_with_tools(rule, now, tools)

            return list(await asyncio.gather(*(observe_bounded(rule) for rule in rules)))

    async def _observe_with_tools(
        self, rule: WatchRule, now: datetime, tools: dict[str, BaseTool]
    ) -> WatchObservation:
        calls = [
            self._invoke(
                tools,
                "sports_state_get_game_state",
                {"game_ref": rule.game.game_ref},
            ),
            self._invoke(
                tools,
                "sports_state_get_play_by_play",
                {"game_ref": rule.game.game_ref, "limit": 20, "play_filter": "scoring"},
            ),
            self._invoke(
                tools,
                "sports_state_get_play_by_play",
                {"game_ref": rule.game.game_ref, "limit": 50, "play_filter": "all"},
            ),
            *[
                self._invoke(
                    tools,
                    f"{market.platform}_get_market",
                    {"market_id": market.market_id},
                )
                for market in rule.markets
            ],
        ]
        results = await asyncio.gather(*calls, return_exceptions=True)

        state = results[0] if isinstance(results[0], GameState) else None
        plays = results[1] if isinstance(results[1], PlayByPlay) else None
        recent = results[2] if isinstance(results[2], PlayByPlay) else None
        market_results = results[3:]
        quote_observations: list[QuoteObservation] = []
        for market, result in zip(rule.markets, market_results, strict=True):
            if not isinstance(result, MarketDetail):
                quote_observations.append(
                    QuoteObservation(
                        platform=market.platform,
                        market_id=market.market_id,
                        outcome=market.outcome,
                        price=None,
                        quote_time=None,
                        retrieved_at=now,
                        status=SourceStatus.MISSING,
                        warning="Market source unavailable or malformed.",
                    )
                )
                continue
            outcome = next(
                (
                    quote
                    for quote in result.outcome_quotes
                    if market.outcome.casefold()
                    in {quote.label.casefold(), (quote.canonical_participant or "").casefold()}
                ),
                None,
            )
            freshness = (
                SourceStatus.STALE
                if result.quote_freshness in {"stale", "not_trading"}
                else SourceStatus.AVAILABLE
            )
            if outcome is None or outcome.price is None:
                freshness = SourceStatus.MALFORMED
            quote_observations.append(
                QuoteObservation(
                    platform=market.platform,
                    market_id=market.market_id,
                    outcome=market.outcome,
                    price=outcome.price if outcome else None,
                    quote_time=result.quote_as_of,
                    retrieved_at=result.retrieved_at,
                    status=freshness,
                    provider_observation_id=result.observation_id,
                    cache_hit=result.cache_hit,
                    warning=(
                        result.quote_freshness_reason
                        if result.quote_freshness != "current"
                        else None
                    ),
                    contract_terminal=result.status.value in {"closed", "resolved", "archived"},
                )
            )
        scoring = []
        missing_play_clock = False
        if plays:
            missing_play_clock = any(
                play.scoring_play and play.wallclock is None for play in plays.plays
            )
            scoring = [
                _summarize(play)
                for play in plays.plays
                if play.scoring_play and play.wallclock is not None
            ]
        # Recent plays are display context only; their absence never blocks evaluation.
        recent_plays = (
            [_summarize(play) for play in recent.plays if _alert_worthy(play)][-20:]
            if recent
            else []
        )
        lifecycle = state.lifecycle if state else "delayed"
        if lifecycle not in {
            "scheduled",
            "pregame",
            "live",
            "halftime",
            "delayed",
            "final",
            "cancelled",
        }:
            lifecycle = "delayed"
        supported_lifecycle = cast(
            Literal[
                "scheduled",
                "pregame",
                "live",
                "halftime",
                "delayed",
                "final",
                "cancelled",
            ],
            lifecycle,
        )
        evidence_identity = {
            "state": state.observation_id if state else None,
            "plays": plays.observation_id if plays else None,
            "recent": recent.observation_id if recent else None,
            "markets": [
                (
                    result.observation_id
                    or result.model_dump(
                        mode="json",
                        include={"market_id", "status", "quote_as_of", "outcome_quotes"},
                    )
                )
                if isinstance(result, MarketDetail)
                else None
                for result in market_results
            ],
        }
        observation_digest = hashlib.blake2s(
            json.dumps(evidence_identity, sort_keys=True).encode(), digest_size=16
        ).hexdigest()
        sports_warning = None
        sports_status = SourceStatus.AVAILABLE
        if not state or not plays:
            sports_warning = "Sports state or scoring feed unavailable."
            sports_status = SourceStatus.MISSING
        elif missing_play_clock:
            sports_warning = (
                "A scoring play lacked a provider play time and was excluded from correlation."
            )
            sports_status = SourceStatus.MALFORMED
        else:
            event_windows = [
                condition.correlation_window_seconds
                for condition in rule.conditions
                if isinstance(condition, PriceMoveCondition)
                and condition.event_relationship != "any"
            ]
            cache_budget_ms = min(event_windows, default=120) * 1000
            cache_age_ms = max(state.cache_age_ms, plays.cache_age_ms)
            if cache_age_ms > cache_budget_ms:
                sports_status = SourceStatus.STALE
                sports_warning = (
                    "Sports or scoring evidence is older than the configured correlation window."
                )
        return WatchObservation(
            observation_id=f"obs_{observation_digest}",
            game_ref=rule.game.game_ref,
            sports_observed_at=(state.provider_updated_at or state.retrieved_at) if state else now,
            retrieved_at=now,
            lifecycle=supported_lifecycle,
            quotes=quote_observations,
            new_scoring_plays=scoring,
            recent_plays=recent_plays,
            sports_status=sports_status,
            sports_warning=sports_warning,
        )


class WatchCoordinator:
    def __init__(self, repository: WatchRepository, evidence: EvidenceProvider) -> None:
        self.repository = repository
        self.evidence = evidence
        self._next_cleanup_at: datetime | None = None

    @staticmethod
    def _group_key(rule: WatchRule) -> tuple[object, ...]:
        markets = tuple(
            sorted((m.platform, m.market_id, m.outcome.casefold()) for m in rule.markets)
        )
        return rule.game.game_ref, markets

    @staticmethod
    def _next_due(observation: WatchObservation, now: datetime) -> datetime:
        if observation.lifecycle in {"live", "halftime"}:
            return now + timedelta(minutes=1)
        if observation.lifecycle in {"final", "cancelled"}:
            return now + timedelta(minutes=15)
        return now + timedelta(minutes=5)

    @staticmethod
    def _outage_observation(rule: WatchRule, observed_at: datetime) -> WatchObservation:
        digest = hashlib.blake2s(
            f"{rule.game.game_ref}:{observed_at.isoformat()}:outage".encode(),
            digest_size=16,
        ).hexdigest()
        return WatchObservation(
            observation_id=f"obs_{digest}",
            game_ref=rule.game.game_ref,
            sports_observed_at=observed_at,
            retrieved_at=observed_at,
            lifecycle="delayed",
            quotes=[],
            sports_status=SourceStatus.MISSING,
            sports_warning="All required evidence sources were unavailable.",
        )

    async def poll(self, *, owner: str, now: datetime | None = None) -> PollResult:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        rules = self.repository.claim_due(owner, observed_at)
        grouped: dict[tuple[object, ...], list[WatchRule]] = defaultdict(list)
        for rule in rules:
            grouped[self._group_key(rule)].append(rule)
        created = duplicates = warnings = 0
        compatible_groups = list(grouped.values())
        representatives = [compatible[0] for compatible in compatible_groups]
        try:
            if not representatives:
                gathered: list[WatchObservation | BaseException] = []
            elif isinstance(self.evidence, BatchEvidenceProvider):
                gathered = list(await self.evidence.observe_many(representatives, observed_at))
            else:
                gathered = list(
                    await asyncio.gather(
                        *(
                            self.evidence.observe(representative, observed_at)
                            for representative in representatives
                        ),
                        return_exceptions=True,
                    )
                )
        except Exception:
            gathered = [RuntimeError("evidence connection unavailable")] * len(representatives)
        for compatible, representative, candidate in zip(
            compatible_groups, representatives, gathered, strict=True
        ):
            observation = (
                candidate
                if isinstance(candidate, WatchObservation)
                else self._outage_observation(representative, observed_at)
            )
            if observation.sports_warning:
                warnings += 1
            for rule in compatible:
                history = self.repository.recent_observations(
                    rule.watch_id, observed_at - timedelta(hours=2)
                )
                self.repository.add_observation(rule.watch_id, observation)
                for condition in rule.conditions:
                    result = evaluate_condition(condition, observation, history)
                    warnings += len(result.warnings)
                    armed, fired_at = self.repository.condition_state(
                        rule.watch_id, condition.condition_id
                    )
                    rearm = getattr(condition, "rearm_below", None)
                    if not result.matched:
                        lifecycle_rearmed = (
                            isinstance(condition, LifecycleCondition)
                            and observation.sports_status == SourceStatus.AVAILABLE
                            and observation.lifecycle not in condition.to_states
                        )
                        metric_rearmed = result.metric is not None and (
                            rearm is None or result.metric < rearm
                        )
                        if not armed and (lifecycle_rearmed or metric_rearmed):
                            self.repository.set_condition_state(
                                rule.watch_id, condition.condition_id, True, fired_at
                            )
                        continue
                    cooldown = getattr(condition, "cooldown_seconds", 0)
                    cooldown_ok = fired_at is None or observed_at >= fired_at + timedelta(
                        seconds=cooldown
                    )
                    if not armed or not cooldown_ok:
                        continue
                    trigger = build_trigger(
                        rule, result, observed_at, recent_plays=observation.recent_plays
                    )
                    if self.repository.record_trigger(rule, trigger):
                        created += 1
                    else:
                        duplicates += 1
                    self.repository.set_condition_state(
                        rule.watch_id, condition.condition_id, False, observed_at
                    )
                current_terminal = (
                    observation.lifecycle in {"final", "cancelled"}
                    and len(observation.quotes) == len(rule.markets)
                    and all(quote.contract_terminal for quote in observation.quotes)
                )
                previous_terminal = bool(
                    history
                    and history[-1].lifecycle in {"final", "cancelled"}
                    and len(history[-1].quotes) == len(rule.markets)
                    and all(quote.contract_terminal for quote in history[-1].quotes)
                )
                if previous_terminal and current_terminal:
                    self.repository.set_status(rule.watch_id, rule.session_id, WatchStatus.TERMINAL)
                else:
                    cadence_observation = observation
                    if observation.sports_status != SourceStatus.AVAILABLE and history:
                        cadence_observation = history[-1]
                    self.repository.release_claim(
                        rule.watch_id, owner, self._next_due(cadence_observation, observed_at)
                    )
        if self._next_cleanup_at is None or observed_at >= self._next_cleanup_at:
            self.repository.cleanup(observed_at)
            self._next_cleanup_at = observed_at + timedelta(hours=1)
        return PollResult(
            claimed_watches=len(rules),
            observation_groups=len(grouped),
            created_triggers=created,
            duplicate_triggers=duplicates,
            source_warnings=warnings,
        )
