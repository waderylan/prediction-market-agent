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
    WatchLifecycleEvent,
    WatchTrigger,
)
from market_agent.watch.service import WatchService

WATCH_TOOL_NAMES = {
    "watch_preview",
    "watch_confirm",
    "watch_list",
    "watch_inspect",
    "watch_pause",
    "watch_resume",
    "watch_inbox",
    "watch_cancel_preview",
    "watch_delete_preview",
    "watch_delete_confirm",
    "watch_event",
    "watch_delivery_status",
    "watch_investigate",
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
    cursor: int = Field(default=0, ge=0, le=200)


class ListInput(LimitInput):
    status: Literal["active", "paused", "terminal", "all"] = "active"
    cursor: int = Field(default=0, ge=0, le=100)


class EventInput(ToolInput):
    event_id: str


class EmptyInput(ToolInput):
    pass


def _event_id(event: WatchLifecycleEvent | Any) -> str:
    return event.event_id if isinstance(event, WatchLifecycleEvent) else event.trigger_id


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
        ("watch_cancel_preview", "Cancel the pending watch preview without saving it.", DraftInput),
        (
            "watch_list",
            "List bounded runtime summaries owned by this chat session. Defaults to active.",
            ListInput,
        ),
        ("watch_inspect", "Inspect one watch and delivery state in this session.", WatchIdInput),
        ("watch_pause", "Pause one watch in this session.", WatchIdInput),
        ("watch_resume", "Resume one watch in this session.", WatchIdInput),
        (
            "watch_delete_preview",
            "Preview deletion of one exact watch; does not delete it.",
            WatchIdInput,
        ),
        ("watch_delete_confirm", "Confirm the exact pending deletion preview.", WatchIdInput),
        (
            "watch_inbox",
            "List deterministic watch alerts and Telegram delivery status.",
            LimitInput,
        ),
        ("watch_event", "Inspect one stored lifecycle event or trigger by event ID.", EventInput),
        (
            "watch_delivery_status",
            "Read stored Telegram delivery status for one event ID.",
            EventInput,
        ),
        (
            "watch_investigate",
            "Read one stored condition trigger before a user-requested evidence investigation.",
            EventInput,
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
    *,
    expected_fingerprint: str | None = None,
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
        return {
            "status": "preview",
            "draft_id": preview.draft_id,
            "fingerprint": preview.fingerprint,
            "preview": preview.text,
        }
    if name == "watch_confirm":
        draft_input = DraftInput.model_validate(raw_arguments)
        rule = service.confirm(
            session_id,
            draft_input.draft_id,
            expected_fingerprint=expected_fingerprint,
        )
        return {
            "status": "saved",
            "runtime_state": "awaiting_first_poll",
            "watch_id": rule.watch_id,
        }
    if name == "watch_cancel_preview":
        draft_input = DraftInput.model_validate(raw_arguments)
        if not service.cancel_preview(session_id, draft_input.draft_id):
            raise KeyError("preview not found for this session")
        return {"status": "cancelled", "draft_id": draft_input.draft_id}
    if name == "watch_list":
        listing = ListInput.model_validate(raw_arguments)
        summaries = service.list_runtime(session_id, listing.status)
        if listing.status == "all":
            summaries.sort(key=lambda item: item.desired_status != "active")
        return {
            "watches": [
                summary.model_dump(mode="json")
                for summary in summaries[listing.cursor : listing.cursor + listing.limit]
            ],
            "next_cursor": listing.cursor + listing.limit
            if len(summaries) > listing.cursor + listing.limit
            else None,
        }
    if name == "watch_inbox":
        limit_input = LimitInput.model_validate(raw_arguments)
        events = service.events(session_id, min(200, limit_input.cursor + limit_input.limit))
        return {
            "events": [
                {
                    "event_id": _event_id(event),
                    "event_kind": getattr(event, "kind", None) or "condition_trigger",
                    "watch_id": event.watch_id,
                    "game": event.game.label,
                    "occurred_at": event.occurred_at
                    if hasattr(event, "occurred_at")
                    else event.triggered_at,
                    "runtime_state": getattr(event, "runtime_state", None),
                    "message": event.message,
                    "delivery_status": service.delivery_status(session_id, _event_id(event)),
                }
                for event in events[limit_input.cursor : limit_input.cursor + limit_input.limit]
            ],
            "next_cursor": limit_input.cursor + limit_input.limit
            if len(events) > limit_input.cursor + limit_input.limit
            else None,
        }
    if name in {"watch_event", "watch_delivery_status", "watch_investigate"}:
        event_id = EventInput.model_validate(raw_arguments).event_id
        if name == "watch_delivery_status":
            return {
                "event_id": event_id,
                "delivery_status": service.delivery_status(session_id, event_id),
            }
        event = service.event(session_id, event_id)
        if name == "watch_investigate" and not isinstance(event, WatchTrigger):
            raise ValueError("Only a stored condition trigger can be investigated.")
        return {
            "event": event.model_dump(mode="json", exclude={"session_id", "fingerprint"}),
            "delivery_status": service.delivery_status(session_id, event_id),
        }
    watch_input = WatchIdInput.model_validate(raw_arguments)
    if name == "watch_inspect":
        return {
            "rule": service.inspect(session_id, watch_input.watch_id).model_dump(
                mode="json", exclude={"session_id"}
            ),
            "runtime": service.runtime(session_id, watch_input.watch_id).model_dump(mode="json"),
        }
    if name == "watch_delete_preview":
        rule = service.inspect(session_id, watch_input.watch_id)
        return {
            "status": "delete_preview",
            "watch_id": rule.watch_id,
            "game": rule.game.label,
            "version": service.version(rule),
            "consequences": ("Stops future polling and removes retained watch history."),
        }
    if name == "watch_delete_confirm":
        raise ValueError("Deletion requires a session-bound preview and exact confirmation.")
    action = {
        "watch_pause": service.pause,
        "watch_resume": service.resume,
    }[name]
    if not action(session_id, watch_input.watch_id):
        raise KeyError("watch not found in this session")
    return {
        "status": "paused" if name == "watch_pause" else "resumed",
        "watch_id": watch_input.watch_id,
    }
