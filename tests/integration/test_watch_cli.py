"""Local Codex JSON-lines workflow uses the shared schema and SQLite service."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from market_agent.watch.cli import WatchCli
from market_agent.watch.models import PriceMoveCondition

pytestmark = pytest.mark.integration


def test_cli_preview_confirm_inspect_and_lifecycle(tmp_path: Path) -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "watch" / "yankees_scoring_replay.json"
        ).read_text(encoding="utf-8")
    )
    cli = WatchCli(str(tmp_path / "watches.db"))
    preview = cli.execute(
        {
            "operation": "preview",
            "session_id": "codex-session",
            "game": fixture["game"],
            "markets": fixture["markets"],
            "conditions": [
                PriceMoveCondition(
                    condition_id="score_move",
                    threshold=Decimal("0.08"),
                    window_seconds=120,
                    event_relationship="scoring_event",
                ).model_dump(mode="json")
            ],
            "delivery": {"channels": ["inbox"]},
        }
    )
    assert "exact game_ref pinned" in preview["preview"]
    assert cli.execute({"operation": "list", "session_id": "codex-session"}) == {"watches": []}
    confirmed = cli.execute(
        {
            "operation": "confirm",
            "session_id": "codex-session",
            "draft_id": preview["draft_id"],
        }
    )
    inspected = cli.execute(
        {
            "operation": "inspect",
            "session_id": "codex-session",
            "watch_id": confirmed["watch_id"],
        }
    )
    assert inspected["watch"]["conditions"][0]["threshold"] == "0.08"
    assert (
        cli.execute(
            {
                "operation": "pause",
                "session_id": "codex-session",
                "watch_id": confirmed["watch_id"],
            }
        )["status"]
        == "pause"
    )
    with pytest.raises(ValueError, match="between 1 and 50"):
        cli.execute({"operation": "inbox", "session_id": "codex-session", "limit": -1})
    with pytest.raises(ValueError, match="canonical watch"):
        cli.execute({"operation": "inspect", "session_id": "codex-session", "watch_id": "bad"})
