"""Bounded model-facing watch commands; host validation remains authoritative."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, cast

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from market_agent.mcp.common import MarketDetail
from market_agent.providers.game_state import GameState
from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    DivergenceCondition,
    GameIdentity,
    LifecycleCondition,
    MarketIdentity,
    PriceMoveCondition,
    WatchCondition,
)
from market_agent.watch.service import WatchService

WATCH_TOOL_NAMES = {
    "watch_preview",
    "watch_confirm",
    "watch_list",
    "watch_inspect",
    "watch_pause",
    "watch_resume",
    "watch_delete",
    "watch_inbox",
}


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketInput(ToolInput):
    platform: Literal["kalshi", "polymarket"]
    market_id: str
    outcome: str


class ConditionInput(ToolInput):
    kind: Literal["price_move", "cross_platform_divergence", "lifecycle_change"]
    condition_id: str
    threshold_points: Decimal | None = Field(default=None, gt=0, le=100)
    window_seconds: int | None = Field(default=None, ge=30, le=3600)
    event_relationship: Literal["scoring_event", "no_tracked_scoring_event", "any"] | None = None
    correlation_window_seconds: int = Field(default=120, ge=30, le=3600)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    rearm_below_points: Decimal | None = Field(default=None, ge=0, le=100)
    to_states: list[Literal["live", "halftime", "delayed", "final", "cancelled"]] = Field(
        default_factory=list
    )


class PreviewInput(ToolInput):
    game_ref: str
    markets: list[MarketInput] = Field(min_length=1, max_length=4)
    conditions: list[ConditionInput] = Field(min_length=1, max_length=8)
    telegram: bool = False
    replaces_watch_id: str | None = None


class DraftInput(ToolInput):
    draft_id: str


class WatchIdInput(ToolInput):
    watch_id: str


class LimitInput(ToolInput):
    limit: int = Field(default=20, ge=1, le=50)


class EmptyInput(ToolInput):
    pass


def _unused(**_: Any) -> str:
    return "The host application executes this command."


def build_watch_tools() -> list[StructuredTool]:
    specifications: list[tuple[str, str, type[BaseModel]]] = [
        (
            "watch_preview",
            "Preview a watch only after exact sports and market detail tools resolve every ID. "
            "Use probability points, not relative percent.",
            PreviewInput,
        ),
        (
            "watch_confirm",
            "Activate the exact pending preview after explicit user confirmation.",
            DraftInput,
        ),
        ("watch_list", "List watches owned by this chat session.", EmptyInput),
        ("watch_inspect", "Inspect one watch and delivery state in this session.", WatchIdInput),
        ("watch_pause", "Pause one watch in this session.", WatchIdInput),
        ("watch_resume", "Resume one watch in this session.", WatchIdInput),
        ("watch_delete", "Delete one watch in this session.", WatchIdInput),
        (
            "watch_inbox",
            "List deterministic watch alerts and Telegram delivery status.",
            LimitInput,
        ),
    ]
    return [
        StructuredTool.from_function(
            _unused,
            name=name,
            description=description,
            args_schema=cast(Any, schema),
        )
        for name, description, schema in specifications
    ]


def _exact_identities(
    arguments: PreviewInput,
    game_states: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> tuple[GameIdentity, list[MarketIdentity]]:
    state_data = next(
        (saved for saved in game_states if saved.get("game_ref") == arguments.game_ref), None
    )
    if state_data is None:
        raise ValueError("Retrieve exact game state before previewing a watch.")
    state = GameState.model_validate(state_data)
    if state.lifecycle in {"final", "cancelled"}:
        raise ValueError("A watch cannot be activated for a terminal game.")
    game = GameIdentity(
        league=state.league,
        game_ref=state.game_ref,
        home_team=state.home_team,
        away_team=state.away_team,
        scheduled_start=state.scheduled_start,
    )
    resolved: list[MarketIdentity] = []
    for requested in arguments.markets:
        data = next(
            (
                saved
                for saved in details
                if saved.get("platform") == requested.platform
                and saved.get("market_id") == requested.market_id
            ),
            None,
        )
        if data is None:
            raise ValueError("Retrieve exact market detail before previewing a watch.")
        detail = MarketDetail.model_validate(data)
        if detail.status.value not in {"unopened", "open", "paused"}:
            raise ValueError("A watch cannot be activated for a terminal contract.")
        sports = detail.sports
        if sports is None or sports.market_type != "game_winner":
            raise ValueError("Only full-game-winner contracts are supported.")
        if (
            sports.league != state.league
            or set(sports.participants) != {state.home_team, state.away_team}
            or sports.scheduled_start is None
            or abs((sports.scheduled_start - state.scheduled_start).total_seconds()) > 900
        ):
            raise ValueError("Market and exact game identities do not match.")
        matching_outcome = next(
            (
                quote
                for quote in detail.outcome_quotes
                if requested.outcome.casefold()
                in {quote.label.casefold(), (quote.canonical_participant or "").casefold()}
            ),
            None,
        )
        if matching_outcome is None:
            raise ValueError("Requested outcome is not an exact named contract outcome.")
        resolved.append(
            MarketIdentity(
                platform=requested.platform,
                market_id=requested.market_id,
                title=detail.title,
                outcome=matching_outcome.canonical_participant or matching_outcome.label,
            )
        )
    return game, resolved


def _conditions(values: list[ConditionInput]) -> list[WatchCondition]:
    compiled: list[WatchCondition] = []
    for value in values:
        threshold = value.threshold_points / 100 if value.threshold_points is not None else None
        rearm = value.rearm_below_points / 100 if value.rearm_below_points is not None else None
        if value.kind == "price_move":
            if (
                threshold is None
                or value.window_seconds is None
                or value.event_relationship is None
            ):
                raise ValueError(
                    "Price movement requires threshold, window, and event relationship."
                )
            compiled.append(
                PriceMoveCondition(
                    condition_id=value.condition_id,
                    threshold=threshold,
                    window_seconds=value.window_seconds,
                    event_relationship=value.event_relationship,
                    correlation_window_seconds=value.correlation_window_seconds,
                    cooldown_seconds=value.cooldown_seconds,
                    rearm_below=rearm,
                )
            )
        elif value.kind == "cross_platform_divergence":
            if threshold is None:
                raise ValueError("Divergence requires a point threshold.")
            compiled.append(
                DivergenceCondition(
                    condition_id=value.condition_id,
                    threshold=threshold,
                    cooldown_seconds=value.cooldown_seconds,
                    rearm_below=rearm,
                )
            )
        else:
            if not value.to_states:
                raise ValueError("Lifecycle condition requires at least one target state.")
            compiled.append(
                LifecycleCondition(
                    condition_id=value.condition_id,
                    to_states=frozenset(value.to_states),
                    cooldown_seconds=value.cooldown_seconds,
                )
            )
    return compiled


def execute_watch_tool(
    service: WatchService,
    session_id: str,
    name: str,
    raw_arguments: dict[str, Any],
    game_states: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    if name == "watch_preview":
        preview_input = PreviewInput.model_validate(raw_arguments)
        game, markets = _exact_identities(preview_input, game_states, details)
        channels = {DeliveryChannel.INBOX}
        if preview_input.telegram:
            channels.add(DeliveryChannel.TELEGRAM)
        preview = service.preview(
            session_id=session_id,
            creation_source="langgraph",
            game=game,
            markets=markets,
            conditions=_conditions(preview_input.conditions),
            delivery=DeliveryPolicy(channels=frozenset(channels)),
            replaces_watch_id=preview_input.replaces_watch_id,
        )
        return {"status": "preview", "draft_id": preview.draft_id, "preview": preview.text}
    if name == "watch_confirm":
        draft_input = DraftInput.model_validate(raw_arguments)
        rule = service.confirm(session_id, draft_input.draft_id)
        return {"status": "active", "watch_id": rule.watch_id}
    if name == "watch_list":
        EmptyInput.model_validate(raw_arguments)
        return {
            "watches": [
                {"watch_id": rule.watch_id, "status": rule.status, "game": rule.game.label}
                for rule in service.list_watches(session_id)
            ]
        }
    if name == "watch_inbox":
        limit_input = LimitInput.model_validate(raw_arguments)

        def delivery_status(trigger_id: str) -> object:
            item = service.repository.outbox_for_trigger(trigger_id)
            return item.status if item else "not_requested"

        return {
            "alerts": [
                {
                    "trigger_id": trigger.trigger_id,
                    "watch_id": trigger.watch_id,
                    "triggered_at": trigger.triggered_at,
                    "message": trigger.message,
                    "telegram": delivery_status(trigger.trigger_id),
                }
                for trigger in service.inbox(session_id, limit_input.limit)
            ]
        }
    watch_input = WatchIdInput.model_validate(raw_arguments)
    if name == "watch_inspect":
        return service.inspect(session_id, watch_input.watch_id).model_dump(mode="json")
    action = {
        "watch_pause": service.pause,
        "watch_resume": service.resume,
        "watch_delete": service.delete,
    }[name]
    if not action(session_id, watch_input.watch_id):
        raise KeyError("watch not found in this session")
    return {"status": name.removeprefix("watch_"), "watch_id": watch_input.watch_id}
