"""Conservative, deterministic comparison of supplied contract terms.

Unknown prose stays unknown. Narrow parsers expose explicit differences; they never
turn a title similarity score into proof of equivalence.
"""

import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from typing import Literal

from pydantic import BaseModel, Field

from market_agent.domain.models import CanonicalMarket, Platform

Verdict = Literal["equivalent", "different", "ambiguous"]
CheckState = Literal["match", "different", "unknown"]
MAX_CANDIDATES = 3


class DimensionCheck(BaseModel):
    dimension: str
    state: CheckState
    reason: str
    left: str | None = None
    right: str | None = None


class PairAssessment(BaseModel):
    polymarket_id: str
    kalshi_id: str
    verdict: Verdict
    relationship: Literal["primary_candidate", "related_context", "unrelated"]
    checks: list[DimensionCheck]
    review_required: bool
    comparison_allowed: bool
    scope: str = "Supplied terms only; external or omitted contract terms are not verified."


class MatchingReport(BaseModel):
    pairs: list[PairAssessment] = Field(max_length=9)
    candidates_per_platform: int = MAX_CANDIDATES
    review_route: str = (
        "Main model explains unresolved terms; it cannot override deterministic rejection."
    )


class ThresholdClause(BaseModel):
    subject: str
    negative: bool
    operator: str
    amount: Decimal
    unit: str
    timing: str
    instant: datetime | None
    resolves_to: str | None


_THRESHOLD = re.compile(
    r"^if (?P<subject>[^.;\n]{1,100}?) is (?P<negative>not )?"
    r"(?P<operator>above|below|at least|at most|greater than|less than|equal to) "
    r"(?P<currency>\$)?(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>USD|dollars|percent|%|degrees C|degrees F|points)? "
    r"(?P<timing>on|by|at) (?P<instant>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)(?=[,.;\s]|$)",
    re.IGNORECASE,
)
_OPERATOR = {
    "above": ">",
    "greater than": ">",
    "below": "<",
    "less than": "<",
    "at least": ">=",
    "at most": "<=",
    "equal to": "=",
}
_UNIT = {"dollars": "usd", "usd": "usd", "%": "percent"}


def normalize(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def threshold_clause(rules: str | None) -> ThresholdClause | None:
    """Only an explicit leading conditional with units and an ISO timestamp is parsed."""
    match = _THRESHOLD.match((rules or "").strip())
    if match is None:
        return None
    unit = normalize(match["unit"])
    if match["currency"]:
        if unit not in ("", "usd", "dollars"):
            return None
        unit = "usd"
    if not unit:
        return None
    try:
        parsed = datetime.fromisoformat(match["instant"].replace("Z", "+00:00"))
        instant = parsed.astimezone(UTC) if parsed.tzinfo else None
        return ThresholdClause(
            subject=normalize(match["subject"]),
            negative=bool(match["negative"]),
            operator=_OPERATOR[normalize(match["operator"])],
            amount=Decimal(match["amount"].replace(",", "")),
            unit=_UNIT.get(unit, unit),
            timing=normalize(match["timing"]),
            instant=instant,
            resolves_to=(
                payout.group(1).lower()
                if (
                    payout := re.match(
                        r",?\s*(?:the market )?resolves? (?:to )?(Yes|No)\b",
                        (rules or "").strip()[match.end() :],
                        re.IGNORECASE,
                    )
                )
                else None
            ),
        )
    except ValueError:
        return None


def _check(
    dimension: str,
    left: object,
    right: object,
    *,
    missing: bool = False,
    mismatch: CheckState = "different",
    reason: str = "",
) -> DimensionCheck:
    state: CheckState = "unknown" if missing else "match" if left == right else mismatch
    return DimensionCheck(
        dimension=dimension,
        state=state,
        reason=reason
        or {
            "unknown": "Not established from supplied terms.",
            "match": "Supplied values agree.",
            "different": "Explicit contract conditions differ.",
        }[state],
        left=str(left)[:300] if left is not None else None,
        right=str(right)[:300] if right is not None else None,
    )


def _settlement_basis(rules: str | None) -> str | None:
    text = normalize(rules)
    has_ap = "associated press" in text or re.search(r"\bap\b", text) is not None
    consensus = (
        has_ap
        and all(source in text for source in ("fox news", "nbc"))
        and bool(
            re.search(r"(?:must|all) call|all (?:three )?.{0,60}call|call the same candidate", text)
        )
    )
    if consensus:
        return "news consensus with fallback" if "inaugurat" in text else "news consensus"
    if re.search(r"^if .{1,100} is (?:the next person )?inaugurated", text):
        return "inauguration"
    return None


def _clauses(rules: str | None, pattern: str) -> tuple[str, ...]:
    return tuple(
        normalize(s)
        for s in re.split(r"[.!?]\s+|\n+", rules or "")
        if re.search(pattern, s, re.IGNORECASE)
    )


def assess_pair(
    polymarket: CanonicalMarket,
    kalshi: CanonicalMarket,
    *,
    polymarket_truncated: bool = False,
    kalshi_truncated: bool = False,
) -> PairAssessment:
    if polymarket.platform != Platform.POLYMARKET or kalshi.platform != Platform.KALSHI:
        raise ValueError("Expected one Polymarket and one Kalshi contract")
    left, right = polymarket, kalshi
    complete = bool(left.rules and right.rules) and not (polymarket_truncated or kalshi_truncated)
    identical_rules = complete and normalize(left.rules) == normalize(right.rules)
    a, b = threshold_clause(left.rules), threshold_clause(right.rules)
    titles = normalize(left.title), normalize(right.title)
    subject_a, subject_b = (a.subject, b.subject) if a and b else titles
    identity = _check(
        "event_identity", subject_a, subject_b, mismatch="different" if a and b else "unknown"
    )
    checks = [identity]
    binary = all(
        len(m.outcomes) == 2 and {normalize(o) for o in m.outcomes} == {"yes", "no"}
        for m in (left, right)
    )
    checks.append(_check("binary_outcomes", binary, True))
    if a and b:
        checks.extend(
            [
                _check(
                    "outcome_polarity",
                    (a.negative, a.resolves_to),
                    (b.negative, b.resolves_to),
                    missing=a.resolves_to is None or b.resolves_to is None,
                ),
                _check("threshold", a.amount, b.amount),
                _check("units", a.unit, b.unit),
                _check("inclusive_boundary", a.operator, b.operator),
                _check(
                    "event_time_utc",
                    a.instant,
                    b.instant,
                    missing=a.instant is None or b.instant is None,
                ),
                _check("timing_condition", a.timing, b.timing),
            ]
        )
    else:
        opposite = titles[0] != titles[1] and (
            titles[0].replace(" not ", " ") == titles[1].replace(" not ", " ")
        )
        checks.append(
            _check(
                "outcome_polarity",
                "opposite" if opposite else None,
                "same",
                missing=not opposite and not identical_rules and titles[0] != titles[1],
            )
        )
        if not opposite and (identical_rules or titles[0] == titles[1]):
            checks[-1] = _check(
                "outcome_polarity", "same supplied predicate", "same supplied predicate"
            )
        for dimension in (
            "threshold",
            "units",
            "inclusive_boundary",
            "event_time_utc",
            "timing_condition",
        ):
            checks.append(
                _check(
                    dimension,
                    None,
                    None,
                    missing=not identical_rules,
                    reason="Identical supplied rules."
                    if identical_rules
                    else "Outside the explicit conditional parser; semantic review required.",
                )
            )

    basis_a, basis_b = _settlement_basis(left.rules), _settlement_basis(right.rules)
    checks.append(
        _check(
            "settlement_trigger",
            basis_a,
            basis_b,
            missing=not identical_rules and (basis_a is None or basis_b is None),
        )
    )
    source_a, source_b = normalize(left.resolution_source), normalize(right.resolution_source)
    checks.append(
        _check(
            "resolution_authority",
            source_a or None,
            source_b or None,
            missing=not source_a or not source_b,
            mismatch="unknown",
            reason="Authority labels require review when absent or different.",
        )
    )
    cancellation_a = _clauses(left.rules, r"cancel|postpon|void")
    cancellation_b = _clauses(right.rules, r"cancel|postpon|void")
    checks.append(
        _check(
            "cancellation",
            cancellation_a,
            cancellation_b,
            missing=not identical_rules and not (cancellation_a and cancellation_b),
            mismatch="unknown",
        )
    )
    # Explicit cancellation payouts are incompatible even when the remaining text is similar.
    payout = re.compile(
        r"(?:^|[.!?]\s+)if (?:the )?event is (?:cancelled|canceled),? "
        r"(?:it |the market )?resolves? (yes|no|void)\b",
        re.IGNORECASE,
    )
    payout_a, payout_b = payout.search(left.rules or ""), payout.search(right.rules or "")
    if payout_a and payout_b:
        checks[-1] = _check("cancellation", payout_a[1].lower(), payout_b[1].lower())
    checks.append(
        _check(
            "exclusions_and_edge_cases",
            _clauses(left.rules, r"except|exclud|tie\b|otherwise|fallback|recount"),
            _clauses(right.rules, r"except|exclud|tie\b|otherwise|fallback|recount"),
            missing=not identical_rules,
            mismatch="unknown",
        )
    )
    checks.append(
        _check(
            "full_rules",
            normalize(left.rules)[:300],
            normalize(right.rules)[:300],
            missing=not complete,
            mismatch="unknown",
        )
    )
    # Compare the FULL strings, not their bounded display previews.
    if complete and not identical_rules:
        checks[-1].state = "unknown"
        checks[-1].reason = "Full rule text differs; unparsed clauses require semantic review."
    checks.append(
        _check(
            "trading_close",
            left.close_time,
            right.close_time,
            missing=left.close_time is None or right.close_time is None,
            mismatch="unknown",
            reason="Trading close is not necessarily the event deadline.",
        )
    )
    checks.append(
        _check(
            "resolution_schedule",
            left.resolution_deadline,
            right.resolution_deadline,
            missing=left.resolution_deadline is None or right.resolution_deadline is None,
            mismatch="unknown",
            reason="Expected resolution time is not necessarily an event cutoff.",
        )
    )

    rejected = any(check.state == "different" for check in checks)
    equivalent = (
        not rejected
        and all(check.state == "match" for check in checks)
        and identical_rules
        and identity.state == "match"
        and binary
        and bool(source_a)
        and source_a == source_b
        and left.close_time == right.close_time
        and left.resolution_deadline == right.resolution_deadline
    )
    verdict: Verdict = "different" if rejected else "equivalent" if equivalent else "ambiguous"
    related = bool(
        set(re.findall(r"[a-z0-9]+", titles[0]))
        & set(re.findall(r"[a-z0-9]+", titles[1]))
        - {"the", "will", "is", "a", "in", "of", "to", "by", "on", "what", "who"}
    )
    return PairAssessment(
        polymarket_id=left.market_id,
        kalshi_id=right.market_id,
        verdict=verdict,
        relationship="primary_candidate"
        if verdict == "equivalent"
        else "related_context"
        if related
        else "unrelated",
        checks=checks,
        review_required=verdict == "ambiguous",
        comparison_allowed=verdict == "equivalent",
    )


def match_candidates(
    polymarket: list[CanonicalMarket],
    kalshi: list[CanonicalMarket],
    *,
    truncated: set[tuple[Platform, str]] | None = None,
) -> MatchingReport:
    """At most three distinct candidates per platform and nine pairs; no hidden API calls."""

    def bounded(markets: list[CanonicalMarket]) -> list[CanonicalMarket]:
        unique: dict[str, CanonicalMarket] = {}
        for market in markets:
            unique.setdefault(market.market_id, market)
            if len(unique) == MAX_CANDIDATES:
                break
        return list(unique.values())

    truncated = truncated or set()
    return MatchingReport(
        pairs=[
            assess_pair(
                a,
                b,
                polymarket_truncated=(a.platform, a.market_id) in truncated,
                kalshi_truncated=(b.platform, b.market_id) in truncated,
            )
            for a, b in product(bounded(polymarket), bounded(kalshi))
        ]
    )


def comparison_notice(report: MatchingReport) -> str:
    """Expose the code's verdict independently of model-generated interpretation."""
    lines = []
    for pair in report.pairs:
        if pair.comparison_allowed:
            finding = "Equivalent supplied terms; external terms remain unverified."
        elif pair.verdict == "different":
            dimensions = ", ".join(
                c.dimension.replace("_", " ") for c in pair.checks if c.state == "different"
            )
            finding = f"Not equivalent: {dimensions}. Treat prices as separate contracts."
        else:
            finding = "Equivalence unverified; semantic review required before comparing prices."
        lines.append(f"- Contract check ({pair.polymarket_id} / {pair.kalshi_id}): {finding}")
    return "\n".join(lines)
