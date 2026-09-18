from datetime import UTC, datetime

import pytest

from market_agent.domain import CanonicalMarket, Platform
from market_agent.domain.matching import assess_pair, match_candidates, threshold_clause
from market_agent.providers.kalshi import _parse_market as parse_kalshi
from market_agent.providers.polymarket import _parse_market as parse_poly

pytestmark = pytest.mark.unit
RULE = (
    "If Bitcoin is above 100000 USD on 2028-01-01T00:00:00Z, the market resolves Yes. Otherwise No."
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
