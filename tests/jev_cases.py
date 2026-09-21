import json
from pathlib import Path
from typing import Any

from market_agent.domain.matching import ContractEvidence
from market_agent.mcp.common import MarketDetail

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


def market_details(case: dict[str, Any]) -> list[MarketDetail]:
    """Project an evaluation pair into the exact MCP detail input contract."""
    details = []
    for contract in contracts(case):
        assert contract.sports is not None
        details.append(
            MarketDetail.model_validate(
                {
                    "platform": contract.platform,
                    "market_id": contract.market_id,
                    "event_id": contract.event_id,
                    "title": contract.title,
                    "status": contract.status,
                    "yes_price": None,
                    "no_price": None,
                    "yes_bid": None,
                    "yes_ask": None,
                    "close_time": None,
                    "source_url": str(contract.source_url),
                    "retrieved_at": contract.retrieved_at,
                    "quote_as_of": None,
                    "quote_is_stale": True,
                    "sports": {
                        "league": contract.sports.league,
                        "provider_event_id": contract.sports.provider_event_id,
                        "raw_title": contract.title,
                        "participants": contract.sports.participants,
                        "raw_participants": contract.sports.participants,
                        "market_type": contract.sports.market_type,
                        "scheduled_start": contract.sports.scheduled_start,
                    },
                    "outcome_quotes": [
                        {
                            **quote.model_dump(mode="json"),
                            "price_kind": "provider_snapshot",
                        }
                        for quote in contract.outcome_quotes
                    ],
                    "outcomes": contract.outcomes,
                    "rules": contract.rules,
                    "rules_truncated": contract.rules_truncated,
                    "resolution_source": contract.resolution_source,
                    "resolution_deadline": contract.resolution_deadline,
                }
            )
        )
    return details
