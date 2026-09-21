import json
from pathlib import Path
from typing import Any

from market_agent.domain.matching import ContractEvidence

DEFAULT_RULES = (
    "Winner is based on the official league result. Overtime is included. "
    "Postponements within two days count. If the game is cancelled, the market resolves void. "
    "If there is a tie, the market resolves void. A shortened official game counts. "
    "If the game is abandoned, the market resolves void."
)
DEFAULT_PARTICIPANTS = ["Carolina Panthers", "Atlanta Falcons"]


def load_cases() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "fixtures" / "jev" / "equivalence_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def contracts(case: dict[str, Any]) -> list[ContractEvidence]:
    output = []
    for platform in ("polymarket", "kalshi"):
        is_poly = platform == "polymarket"
        prefix = "left" if is_poly else "right"
        participants = case.get(f"{prefix}_participants", DEFAULT_PARTICIPANTS)
        rules = case.get(f"{prefix}_rules", DEFAULT_RULES)
        output.append(
            ContractEvidence.model_validate(
                {
                    "platform": platform,
                    "market_id": f"{platform}-{case['name']}",
                    "event_id": f"{platform}-event",
                    "title": "Panthers vs Falcons" if is_poly else "Panthers win",
                    "outcomes": participants if is_poly else ["Yes", "No"],
                    "status": "open",
                    "rules": rules,
                    "rules_truncated": case.get(f"{prefix}_truncated", False),
                    "resolution_source": case.get(
                        f"{prefix}_resolution_source", "https://www.nfl.com/scores"
                    ),
                    "source_url": "https://example.test/market",
                    "retrieved_at": "2026-09-20T16:00:00Z",
                    "sports": {
                        "league": "nfl",
                        "provider_event_id": f"{platform}-event",
                        "participants": participants,
                        "market_type": "game_winner",
                        "scheduled_start": case.get(f"{prefix}_start", "2026-09-20T17:00:00Z"),
                        "season": "2026",
                        "game_number": case.get(f"{prefix}_game_number"),
                    },
                    "outcome_quotes": (
                        [{"label": team, "canonical_participant": team} for team in participants]
                        if is_poly
                        else [
                            {
                                "label": participants[0],
                                "side": "yes",
                                "canonical_participant": participants[0],
                            },
                            {"label": f"Not {participants[0]}", "side": "no"},
                        ]
                    ),
                }
            )
        )
    return output
