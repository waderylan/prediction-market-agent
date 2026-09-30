"""Core command handler used by the local JSON-lines watch CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from market_agent.watch.models import (
    DeliveryPolicy,
    GameIdentity,
    MarketIdentity,
    WatchCondition,
    WatchRule,
    WatchTrigger,
)
from market_agent.watch.repository import SQLiteWatchRepository
from market_agent.watch.service import WatchService


class WatchCli:
    def __init__(self, database: str, *, telegram_configured: bool = False) -> None:
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database = str(path)
        self.service = WatchService(
            SQLiteWatchRepository(path), telegram_configured=telegram_configured
        )

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        session_id = str(request.get("session_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise ValueError("session_id must use 1-128 letters, digits, underscores, or hyphens")
        if operation == "validate":
            rule = WatchRule.model_validate_json(json.dumps(request["rule"]))
            return {"valid": True, "schema_version": rule.schema_version}
        if operation == "preview":
            preview = self.service.preview(
                session_id=session_id,
                creation_source="codex",
                game=GameIdentity.model_validate_json(json.dumps(request["game"])),
                markets=[
                    MarketIdentity.model_validate_json(json.dumps(item))
                    for item in request["markets"]
                ],
                conditions=TypeAdapter(list[WatchCondition]).validate_json(
                    json.dumps(request["conditions"])
                ),
                delivery=DeliveryPolicy.model_validate_json(
                    json.dumps(request.get("delivery", {}))
                ),
                replaces_watch_id=request.get("replaces_watch_id"),
            )
            return {"draft_id": preview.draft_id, "preview": preview.text}
        if operation == "confirm":
            rule = self.service.confirm(session_id, str(request["draft_id"]))

            def command(*arguments: str) -> str:
                return subprocess.list2cmdline(
                    ["uv", "run", "--env-file", ".env", "python", *arguments]
                )

            return {
                "watch_id": rule.watch_id,
                "status": rule.status,
                "runtime_state": "awaiting_first_poll",
                "receipt": "Saved. Monitoring begins only after the first usable poll.",
                "runner_command": command("scripts/run_watches.py", "--db", self.database),
                "list_command": command(
                    "scripts/watch_cli.py",
                    "--db",
                    self.database,
                    "--operation",
                    "list",
                    "--session-id",
                    session_id,
                    "--status",
                    "active",
                ),
                "inspect_command": command(
                    "scripts/watch_cli.py",
                    "--db",
                    self.database,
                    "--operation",
                    "inspect",
                    "--session-id",
                    session_id,
                    "--watch-id",
                    rule.watch_id,
                ),
            }
        watch_id = str(request.get("watch_id", ""))
        if operation in {"inspect", "pause", "resume", "delete"} and not re.fullmatch(
            r"watch_[a-f0-9]{32}", watch_id
        ):
            raise ValueError("watch_id must be a canonical watch identifier")
        if operation == "list":
            return {
                "watches": [
                    summary.model_dump(mode="json")
                    for summary in self.service.list_runtime(
                        session_id, str(request.get("status", "all"))
                    )
                ]
            }
        if operation == "inspect":
            rule = self.service.inspect(session_id, watch_id)
            return {
                "watch": rule.model_dump(mode="json"),
                "runtime": self.service.runtime(session_id, watch_id).model_dump(mode="json"),
            }
        if operation == "inbox":
            limit = int(request.get("limit", 20))
            if not 1 <= limit <= 50:
                raise ValueError("limit must be between 1 and 50")
            events = self.service.events(session_id, limit)
            return {
                "alerts": [
                    {
                        **event.model_dump(mode="json"),
                        "kind": "condition_triggered"
                        if isinstance(event, WatchTrigger)
                        else event.kind.value,
                        "delivery": (
                            item.model_dump(mode="json")
                            if (
                                item := self.service.repository.outbox_for_trigger(
                                    event.trigger_id
                                    if isinstance(event, WatchTrigger)
                                    else event.event_id
                                )
                            )
                            else {"status": "inbox_only"}
                        ),
                    }
                    for event in events
                ]
            }
        action = {
            "pause": self.service.pause,
            "resume": self.service.resume,
            "delete": self.service.delete,
        }.get(str(operation))
        if action is None:
            raise ValueError("unsupported operation")
        if not action(session_id, watch_id):
            raise KeyError("watch not found in this session")
        return {"watch_id": watch_id, "status": operation}
