from datetime import UTC, datetime

import pytest

from market_agent.domain import CanonicalMarket, Platform
from market_agent.domain.matching import (
    ContractEvidence,
    GameEvidence,
    assess_market_game,
    assess_pair,
    match_candidates,
    threshold_clause,
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


def market(platform, **changes):
    data = {
        "platform": platform,
        "market_id": "1" if platform == "polymarket" else "KXTEST-1",
        "title": "Bitcoin above 100000 USD",
        "outcomes": ["Yes", "No"],
        "status": "open",
        "rules": RULE,
        "resolution_source": "https://example.test/official",
        "close_time": "2028-01-01T00:00:00Z",
        "resolution_deadline": "2028-01-02T00:00:00Z",
        "source_url": "https://example.test/market",
        "retrieved_at": datetime.now(UTC),
    }
    data.update(changes)
    return CanonicalMarket.model_validate(data)


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


def test_identical_supplied_terms_are_primary_candidates():
    report = assess_pair(market("polymarket"), market("kalshi"))
    assert report.verdict == "equivalent"
    assert report.comparison_allowed and not report.review_required
    assert report.relationship == "primary_candidate"


@pytest.mark.parametrize(
    "before,after,dimension",
    [
        ("Bitcoin", "Ethereum", "event_identity"),
        ("100000", "110000", "threshold"),
        ("USD", "percent", "units"),
        ("above", "at least", "inclusive_boundary"),
        ("above", "below", "inclusive_boundary"),
        ("is above", "is not above", "outcome_polarity"),
        ("resolves Yes", "resolves No", "outcome_polarity"),
        ("2028-01-01", "2028-01-02", "event_time_utc"),
        (" on ", " by ", "timing_condition"),
    ],
)
def test_explicit_dangerous_mismatches(before, after, dimension):
    report = assess_pair(market("polymarket"), market("kalshi", rules=RULE.replace(before, after)))
    assert report.verdict == "different"
    assert not report.comparison_allowed
    assert check(report, dimension).state == "different"


def test_timezones_normalize_but_do_not_prove_unparsed_rules_equivalent():
    equivalent_instant = RULE.replace("2028-01-01T00:00:00Z", "2027-12-31T19:00:00-05:00")
    report = assess_pair(market("polymarket"), market("kalshi", rules=equivalent_instant))
    assert check(report, "event_time_utc").state == "match"
    assert report.verdict == "ambiguous"  # full prose still differs


def test_naive_dates_are_unknown_not_local_machine_timezone():
    rules = RULE.replace("00:00:00Z", "00:00:00")
    report = assess_pair(market("polymarket", rules=rules), market("kalshi", rules=rules))
    assert check(report, "event_time_utc").state == "unknown"
    assert report.verdict == "ambiguous"


def test_trading_close_is_not_an_event_cutoff():
    report = assess_pair(market("polymarket"), market("kalshi", close_time="2029-01-01T00:00:00Z"))
    assert report.verdict == "ambiguous"
    assert check(report, "trading_close").state == "unknown"
    assert check(report, "event_time_utc").state == "match"


def test_cancellation_payout_conflict_is_rejected():
    left = RULE + " If the event is cancelled, it resolves No."
    right = RULE + " If the event is cancelled, it resolves Yes."
    report = assess_pair(market("polymarket", rules=left), market("kalshi", rules=right))
    assert check(report, "cancellation").state == "different"
    assert report.verdict == "different"


@pytest.mark.parametrize(
    "changes",
    [
        {"rules": None},
        {"resolution_source": None},
        {"resolution_source": "https://another.test/official"},
        {"rules": RULE + " Excludes preliminary measurements."},
        {"rules": RULE + " In a tie the market is void."},
    ],
)
def test_missing_or_unparsed_terms_route_for_review(changes):
    report = assess_pair(market("polymarket"), market("kalshi", **changes))
    assert report.verdict == "ambiguous"
    assert report.review_required and not report.comparison_allowed
    assert report.relationship == "related_context"


def test_rule_prefix_match_does_not_hide_late_difference():
    prefix = RULE + " Standard context." * 40
    report = assess_pair(
        market("polymarket", rules=prefix + " Except recounts."),
        market("kalshi", rules=prefix + " Including recounts."),
    )
    assert report.verdict == "ambiguous"
    assert check(report, "full_rules").state == "unknown"


def test_truncation_and_nonbinary_are_not_equivalent():
    assert (
        assess_pair(market("polymarket"), market("kalshi"), polymarket_truncated=True).verdict
        == "ambiguous"
    )
    report = assess_pair(market("polymarket", outcomes=["Alice", "Bob"]), market("kalshi"))
    assert report.verdict == "different"


def test_polarity_from_title():
    report = assess_pair(
        market("polymarket", title="Will the event occur?", rules="Settles as titled."),
        market("kalshi", title="Will the event not occur?", rules="Settles as titled."),
    )
    assert report.verdict == "different"


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


def test_observed_authority_and_inauguration_wording():
    left = (
        "Associated Press, Fox News, and NBC are sources. All three sources call the race. "
        "Inauguration is the fallback."
    )
    right = (
        "If J.D. Vance is the next person inaugurated as President, "
        "then the market resolves to Yes."
    )
    report = assess_pair(market("polymarket", rules=left), market("kalshi", rules=right))
    assert check(report, "settlement_trigger").state == "different"


def test_bounded_pairs_and_deduplication():
    polys = [market("polymarket", market_id=str(i)) for i in range(10)]
    kalshis = [market("kalshi", market_id=f"KX-{i}") for i in range(10)]
    report = match_candidates([polys[0], *polys], kalshis)
    assert len(report.pairs) == 9
    assert len({p.polymarket_id for p in report.pairs}) == 3
    assert match_candidates([], kalshis).pairs == []
    assert (
        not match_candidates(polys, kalshis, truncated={(Platform.POLYMARKET, "0")})
        .pairs[0]
        .comparison_allowed
    )


def test_unsupported_prose_and_wrong_platform():
    assert threshold_clause("If an outcome seems likely, resolve Yes") is None
    with pytest.raises(ValueError, match="Expected one"):
        assess_pair(market("kalshi"), market("polymarket"))


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


def test_sports_start_drift_within_thirty_minutes_and_separate_clocks_are_accepted():
    pair = assess_pair(
        sports_market(
            "polymarket",
            close_time="2026-09-20T00:00:00Z",
            resolution_deadline="2026-09-22T00:00:00Z",
        ),
        sports_market(
            "kalshi",
            scheduled_start="2026-09-20T00:40:00Z",
            close_time="2026-09-20T01:00:00Z",
            resolution_deadline="2026-09-24T00:00:00Z",
        ),
    )

    assert pair.verdict == "equivalent"
    assert pair.event_identity is not None
    assert (
        next(c for c in pair.event_identity.checks if c.dimension == "scheduled_start").state
        == "match"
    )
    assert check(pair, "trading_close").state == "unknown"
    assert check(pair, "trading_close").required is False
    assert check(pair, "resolution_deadline").state == "unknown"


def test_sports_settlement_conflict_is_related_context_not_equivalent():
    conflicting = SPORTS_RULE.replace("market resolves void", "market resolves Yes", 1)
    pair = assess_pair(sports_market("polymarket"), sports_market("kalshi", rules=conflicting))

    assert pair.verdict == "different"
    assert pair.relationship == "related_context"
    assert check(pair, "cancellation_payout").state == "different"
    assert not pair.comparison_allowed


def test_live_authority_formatting_matches_domains_and_real_rule_conflict_is_decisive():
    pair = assess_pair(
        sports_market(
            "polymarket",
            rules=LIVE_POLY_RULE,
            resolution_source="https://www.nfl.com/scores",
        ),
        sports_market(
            "kalshi",
            rules=LIVE_KALSHI_RULE,
            resolution_source="the Governing League (https://www.nfl.com/)",
        ),
    )

    assert check(pair, "resolution_authority").state == "match"
    assert check(pair, "postponement_window").state == "different"
    assert check(pair, "tie_treatment").state == "match"
    assert pair.verdict == "different"


def test_overlapping_authority_sets_are_ambiguous_not_a_false_conflict():
    pair = assess_pair(
        sports_market(
            "polymarket",
            resolution_source="https://www.mlb.com/",
        ),
        sports_market(
            "kalshi",
            resolution_source=(
                "ESPN (https://www.espn.com); Fox Sports (https://www.foxsports.com); "
                "the Governing League (https://www.mlb.com/)"
            ),
        ),
    )

    authority = check(pair, "resolution_authority")
    assert authority.state == "unknown"
    assert "overlap" in authority.reason
    assert pair.verdict == "ambiguous"


def test_only_exclusive_disjoint_authorities_are_deterministically_different():
    ambiguous = assess_pair(
        sports_market("polymarket", resolution_source="https://source-a.example/results"),
        sports_market("kalshi", resolution_source="https://source-b.example/results"),
    )
    conflicting = assess_pair(
        sports_market(
            "polymarket", resolution_source="Resolves solely by https://source-a.example/results"
        ),
        sports_market(
            "kalshi", resolution_source="Resolves only by https://source-b.example/results"
        ),
    )

    assert check(ambiguous, "resolution_authority").state == "unknown"
    assert ambiguous.verdict == "ambiguous"
    assert check(conflicting, "resolution_authority").state == "different"
    assert conflicting.verdict == "different"


def test_fair_price_cancellation_conflicts_with_fifty_fifty():
    kalshi_rules = LIVE_KALSHI_RULE + (
        " If the game is cancelled, the market resolves to a fair price."
    )
    pair = assess_pair(
        sports_market("polymarket", rules=LIVE_POLY_RULE),
        sports_market("kalshi", rules=kalshi_rules),
    )

    assert check(pair, "cancellation_payout").state == "different"
    assert check(pair, "cancellation_payout").left == "50_50"
    assert check(pair, "cancellation_payout").right == "fair_value"


def test_missing_sports_settlement_evidence_is_ambiguous_and_routes_to_future_review():
    pair = assess_pair(
        sports_market("polymarket"),
        sports_market("kalshi", rules="Full game winner.", resolution_source=None),
    )

    assert pair.verdict == "ambiguous"
    assert pair.review_required
    assert not pair.comparison_allowed
    assert pair.semantic_review_route == "milestone_8_jev_not_integrated"
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


def test_market_to_game_conflict_and_missing_market_sports_are_explicit():
    conflict = assess_market_game(
        sports_market("polymarket"),
        game(home_team="Boston Red Sox", scheduled_start="2026-09-20T02:00:00Z"),
    )
    missing = assess_market_game(market("polymarket"), game())

    assert conflict.verdict == "different"
    assert not conflict.use_together
    assert {c.dimension for c in conflict.checks if c.state == "different"} == {
        "participants",
        "scheduled_start",
    }
    assert missing.verdict == "insufficient_evidence"
    assert missing.checks[0].state == "insufficient_evidence"


def test_sports_state_unavailability_does_not_block_equivalence():
    report = match_candidates([sports_market("polymarket")], [sports_market("kalshi")], games=[])
    assert report.pairs[0].verdict == "equivalent"
    assert report.market_to_game == []
