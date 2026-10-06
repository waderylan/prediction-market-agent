from datetime import UTC, datetime

import pytest

from market_agent.domain.matching import (
    ContractEvidence,
    GameEvidence,
    assess_pair,
    match_candidates,
)
from market_agent.providers.kalshi import _parse_market as parse_kalshi
from market_agent.providers.polymarket import _parse_market as parse_poly

pytestmark = pytest.mark.unit
RULE = (
    "If Bitcoin is above 100000 USD on 2028-01-01T00:00:00Z, the market resolves Yes. Otherwise No."
)
SPORTS_RULE = (
    "Winner is based on the official league result. Overtime is included. "
    "Postponements within two days count. If the game is cancelled, the market resolves void. "
    "If there is a tie, the market resolves void. A shortened official game counts. "
    "If the game is abandoned, the market resolves void."
)
LIVE_POLY_RULE = (
    "If Panthers wins, this market resolves to Panthers. "
    "If the game is postponed, this market will remain open until the game has been completed. "
    "If the game is canceled entirely or ends in a tie, this market resolves 50-50."
)
LIVE_KALSHI_RULE = (
    "If Atlanta wins, this market resolves to Yes. "
    "If the game ends in a tie, the market resolves to $0.50 for each team. "
    "If the game is postponed but begins within 48 hours, it resolves on the official final "
    "result. "
    "If the game is not started within 48 hours, it resolves to a fair price."
)


def check(report, dimension):
    return next(c for c in report.checks if c.dimension == dimension)


def sports_market(platform, **changes):
    is_poly = platform == "polymarket"
    participants = changes.pop("participants", ["New York Yankees", "San Diego Padres"])
    start = changes.pop("scheduled_start", "2026-09-20T00:10:00Z")
    target = changes.pop("target", "New York Yankees")
    game_number = changes.pop("game_number", None)
    data = {
        "platform": platform,
        "market_id": "201" if is_poly else "KXMLBGAME-OPAQUE-0",
        "event_id": "101" if is_poly else "KXMLBGAME-OPAQUE",
        "title": "Yankees vs Padres" if is_poly else "Yankees win",
        "outcomes": participants if is_poly else ["Yes", "No"],
        "status": "open",
        "rules": SPORTS_RULE,
        "rules_truncated": False,
        "resolution_source": "MLB official results (https://www.mlb.com/)",
        "close_time": "2026-09-20T00:10:00Z",
        "resolution_deadline": "2026-09-23T00:10:00Z",
        "source_url": "https://example.test/market",
        "retrieved_at": "2026-09-19T23:00:00Z",
        "sports": {
            "league": "mlb",
            "provider_event_id": "101" if is_poly else "KXMLBGAME-OPAQUE",
            "participants": participants,
            "market_type": "game_winner",
            "line": None,
            "scheduled_start": start,
            "schedule_source": "provider_schedule",
            "game_number": game_number,
        },
        "outcome_quotes": (
            [
                {"label": participant, "canonical_participant": participant}
                for participant in participants
            ]
            if is_poly
            else [
                {"label": target, "side": "yes", "canonical_participant": target},
                {"label": f"Not {target}", "side": "no"},
            ]
        ),
    }
    data.update(changes)
    return ContractEvidence.model_validate(data)


def game(**changes):
    data = {
        "league": "mlb",
        "game_ref": "opaque-ref",
        "source": "espn",
        "provider_game_id": "401",
        "home_team": "San Diego Padres",
        "away_team": "New York Yankees",
        "scheduled_start": "2026-09-20T00:20:00Z",
        "retrieved_at": "2026-09-20T03:00:00Z",
        "lifecycle": "final",
    }
    data.update(changes)
    return GameEvidence.model_validate(data)


def test_real_near_match_has_incompatible_settlement_trigger(load_fixture):
    poly = parse_poly(
        load_fixture("polymarket", "market_success"),
        event_id="31552",
        retrieved_at=datetime.now(UTC),
    )
    kalshi = parse_kalshi(
        load_fixture("kalshi", "market_success")["market"], retrieved_at=datetime.now(UTC)
    )
    report = assess_pair(poly, kalshi)
    assert report.verdict == "different"
    assert check(report, "settlement_trigger").state == "different"
    assert report.relationship == "related_context"
    assert not report.comparison_allowed


def test_sports_equivalence_uses_typed_identity_outcomes_and_rules_without_game_state():
    report = match_candidates([sports_market("polymarket")], [sports_market("kalshi")])
    pair = report.pairs[0]

    assert pair.verdict == "equivalent"
    assert pair.relationship == "primary_candidate"
    assert pair.comparison_allowed
    assert pair.event_identity is not None and pair.event_identity.verdict == "match"
    assert pair.contract_equivalence is not None
    assert pair.contract_equivalence.verdict == "equivalent"
    mapping = check(pair, "named_outcome_mapping")
    assert mapping.state == "match"
    assert "NO is not inferred" in mapping.reason
    assert report.market_to_game == []


@pytest.mark.parametrize(
    "left_changes,right_changes,dimension",
    [
        ({}, {"participants": ["New York Yankees", "Boston Red Sox"]}, "participants"),
        (
            {"game_number": 1},
            {"game_number": 2},
            "game_number",
        ),
        (
            {},
            {"scheduled_start": "2026-09-20T00:41:00Z"},
            "scheduled_start",
        ),
    ],
)
def test_sports_event_dangerous_near_matches_are_rejected(left_changes, right_changes, dimension):
    pair = assess_pair(
        sports_market("polymarket", **left_changes),
        sports_market("kalshi", **right_changes),
    )

    assert pair.verdict == "different"
    assert pair.relationship == "unrelated"
    assert pair.event_identity is not None
    identity_check = next(c for c in pair.event_identity.checks if c.dimension == dimension)
    assert identity_check.state == "different"


def test_sports_settlement_conflict_is_related_context_not_equivalent():
    conflicting = SPORTS_RULE.replace("market resolves void", "market resolves Yes", 1)
    pair = assess_pair(sports_market("polymarket"), sports_market("kalshi", rules=conflicting))

    assert pair.verdict == "different"
    assert pair.relationship == "related_context"
    assert check(pair, "cancellation_payout").state == "different"
    assert not pair.comparison_allowed


def test_missing_sports_settlement_evidence_remains_ambiguous():
    pair = assess_pair(
        sports_market("polymarket"),
        sports_market("kalshi", rules="Full game winner.", resolution_source=None),
    )

    assert pair.verdict == "ambiguous"
    assert pair.review_required
    assert not pair.comparison_allowed
    assert pair.contract_equivalence is not None
    assert any(c.state == "unknown" for c in pair.contract_equivalence.checks)


def test_market_to_game_report_is_typed_and_game_result_cannot_change_contract_verdict():
    poly = sports_market("polymarket")
    kalshi = sports_market(
        "kalshi",
        rules=SPORTS_RULE.replace("market resolves void", "market resolves Yes", 1),
    )
    report = match_candidates([poly], [kalshi], games=[game(lifecycle="final")])

    assert report.pairs[0].verdict == "different"
    assert len(report.market_to_game) == 2
    assert {assessment.verdict for assessment in report.market_to_game} == {"match"}
    assert all(assessment.use_together for assessment in report.market_to_game)
    assert all(assessment.game_source == "espn" for assessment in report.market_to_game)
    assert all(assessment.game_lifecycle == "final" for assessment in report.market_to_game)
    assert "does not establish contract equivalence" in report.market_to_game[0].scope
