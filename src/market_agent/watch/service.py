"""Single application service for preview, confirmation, lifecycle, and inbox flows."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from market_agent.watch.models import (
    DeliveryChannel,
    DeliveryPolicy,
    GameIdentity,
    MarketIdentity,
    WatchCondition,
    WatchRule,
    WatchStatus,
    WatchTrigger,
    utc_now,
)
from market_agent.watch.repository import WatchRepository


class WatchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft_id: str = Field(pattern=r"^draft_[a-f0-9]{32}$")
    session_id: str
    creation_source: str
    game: GameIdentity
    markets: list[MarketIdentity] = Field(min_length=1, max_length=4)
    conditions: list[WatchCondition] = Field(min_length=1, max_length=8)
    delivery: DeliveryPolicy
    created_at: datetime
    replaces_watch_id: str | None = None


@dataclass(frozen=True)
class WatchPreview:
    draft_id: str
    text: str


class WatchService:
    """Validated owner of watch state; model output cannot write storage directly."""

    def __init__(self, repository: WatchRepository, *, telegram_configured: bool = False) -> None:
        self.repository = repository
        self.telegram_configured = telegram_configured
        self._drafts: dict[tuple[str, str], WatchDraft] = {}
        self._lock = threading.RLock()

    def preview(
        self,
        *,
        session_id: str,
        creation_source: str,
        game: GameIdentity,
        markets: list[MarketIdentity],
        conditions: list[WatchCondition],
        delivery: DeliveryPolicy,
        replaces_watch_id: str | None = None,
    ) -> WatchPreview:
        if creation_source not in {"langgraph", "codex", "cli"}:
            raise ValueError("unsupported creation source")
        if replaces_watch_id and not self.repository.get_rule(replaces_watch_id, session_id):
            raise KeyError("watch not found in this session")
        created_at = utc_now()
        draft = WatchDraft(
            draft_id=f"draft_{uuid4().hex}",
            session_id=session_id,
            creation_source=creation_source,
            game=game,
            markets=markets,
            conditions=conditions,
            delivery=delivery,
            created_at=created_at,
            replaces_watch_id=replaces_watch_id,
        )
        # Build the final rule once as a deterministic semantic-validation dry run.
        self._rule_from_draft(draft, confirmed_at=draft.created_at)
        with self._lock:
            self._prune_drafts(created_at)
            self._drafts = {
                key: value for key, value in self._drafts.items() if key[0] != session_id
            }
            self._drafts[(session_id, draft.draft_id)] = draft
            if len(self._drafts) > 1024:
                oldest = min(self._drafts, key=lambda key: self._drafts[key].created_at)
                del self._drafts[oldest]
        return WatchPreview(draft.draft_id, self._render_preview(draft))

    def confirm(self, session_id: str, draft_id: str, *, now: datetime | None = None) -> WatchRule:
        with self._lock:
            self._prune_drafts(utc_now())
            draft = self._drafts.get((session_id, draft_id))
            if draft is None:
                raise KeyError("preview not found for this session")
            rule = self._rule_from_draft(draft, confirmed_at=now or utc_now())
            if draft.replaces_watch_id:
                self.repository.replace_rule(rule, session_id)
            else:
                self.repository.save_rule(rule, due_at=rule.monitoring_starts_at)
            del self._drafts[(session_id, draft_id)]
            return rule

    def _prune_drafts(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=30)
        self._drafts = {
            key: draft for key, draft in self._drafts.items() if draft.created_at >= cutoff
        }

    def list_watches(self, session_id: str) -> list[WatchRule]:
        return self.repository.list_rules(session_id)

    def inspect(self, session_id: str, watch_id: str) -> WatchRule:
        rule = self.repository.get_rule(watch_id, session_id)
        if rule is None:
            raise KeyError("watch not found in this session")
        return rule

    def pause(self, session_id: str, watch_id: str) -> bool:
        return self.repository.set_status(watch_id, session_id, WatchStatus.PAUSED)

    def resume(self, session_id: str, watch_id: str) -> bool:
        return self.repository.set_status(watch_id, session_id, WatchStatus.ACTIVE)

    def delete(self, session_id: str, watch_id: str) -> bool:
        return self.repository.delete_rule(watch_id, session_id)

    def inbox(self, session_id: str, limit: int = 20) -> list[WatchTrigger]:
        return self.repository.list_triggers(session_id, limit)

    def _rule_from_draft(self, draft: WatchDraft, confirmed_at: datetime) -> WatchRule:
        watch_id = draft.replaces_watch_id or f"watch_{uuid4().hex}"
        return WatchRule(
            watch_id=watch_id,
            session_id=draft.session_id,
            status=WatchStatus.ACTIVE,
            confirmed_at=confirmed_at,
            creation_source=draft.creation_source,  # type: ignore[arg-type]
            game=draft.game,
            markets=draft.markets,
            conditions=draft.conditions,
            delivery=draft.delivery,
        )

    def _render_preview(self, draft: WatchDraft) -> str:
        conditions: list[str] = []
        for condition in draft.conditions:
            if condition.kind == "price_move":
                relationship = condition.event_relationship.replace("_", " ")
                rearm = (
                    f"below {condition.rearm_below * 100:g} points"
                    if condition.rearm_below is not None
                    else "after a verified below-threshold observation"
                )
                conditions.append(
                    f"{condition.threshold * 100:g} probability points within "
                    f"{condition.window_seconds}s ({relationship}; correlation window "
                    f"{condition.correlation_window_seconds}s; cooldown "
                    f"{condition.cooldown_seconds}s; re-arm {rearm})"
                )
            elif condition.kind == "cross_platform_divergence":
                rearm = (
                    f"below {condition.rearm_below * 100:g} points"
                    if condition.rearm_below is not None
                    else "after a verified below-threshold observation"
                )
                conditions.append(
                    f"cross-platform divergence of {condition.threshold * 100:g} points "
                    f"(cooldown {condition.cooldown_seconds}s; re-arm {rearm})"
                )
            else:
                conditions.append(
                    "lifecycle changes to "
                    + ", ".join(sorted(condition.to_states))
                    + f" (cooldown {condition.cooldown_seconds}s)"
                )
        channels = ", ".join(sorted(channel.value for channel in draft.delivery.channels))
        telegram_note = ""
        if DeliveryChannel.TELEGRAM in draft.delivery.channels and not self.telegram_configured:
            telegram_note = " Telegram is not configured, so inbox fallback remains active."
        contracts = "; ".join(
            f"{market.platform} {market.market_id} ({market.outcome})" for market in draft.markets
        )
        revision = (
            f"Revision: replaces `{draft.replaces_watch_id}` only after confirmation.\n"
            if draft.replaces_watch_id
            else ""
        )
        return (
            f"Watch preview `{draft.draft_id}`\n"
            f"{revision}"
            f"Game: {draft.game.label} - {draft.game.scheduled_start.isoformat()} "
            f"[{draft.game.league}; exact game_ref pinned]\n"
            f"Contracts: {contracts}\n"
            f"Triggers: {'; '.join(conditions)}\n"
            f"Delivery: {channels}.{telegram_note}\n"
            "Polling: starts 15m before; 5m pregame/delay; 1m active; one final poll; stops "
            "15m after the game and all contracts are terminal; zero model or Tavily calls.\n"
            "This preview expires in 30 minutes and replaces any older pending preview in this "
            "session.\n"
            f"Reply with the exact draft ID to confirm: confirm {draft.draft_id}"
        )
