"""Conservative, deterministic comparison of supplied contract terms.

Unknown prose stays unknown. Narrow parsers expose explicit differences; they never
turn a title similarity score into proof of equivalence.
"""

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Literal

from pydantic import BaseModel, Field

from market_agent.domain.models import CanonicalMarket, Platform

Verdict = Literal["equivalent", "different", "ambiguous"]
CheckState = Literal["match", "different", "unknown"]
IdentityState = Literal["match", "different", "insufficient_evidence"]
MAX_CANDIDATES = 3
START_TOLERANCE = timedelta(minutes=30)


class DimensionCheck(BaseModel):
    dimension: str
    state: CheckState
    reason: str
    left: str | None = None
    right: str | None = None
    required: bool = True
    left_provenance: str | None = None
    right_provenance: str | None = None


class IdentityDimensionCheck(BaseModel):
    dimension: str
    state: IdentityState
    reason: str
    market_value: str | None = None
    comparison_value: str | None = None
    market_provenance: str
    comparison_provenance: str
    required: bool = True


class SportsIdentityEvidence(BaseModel):
    league: str
    provider_event_id: str
    participants: tuple[str, str]
    market_type: str
    line: Decimal | None = None
    scheduled_start: datetime | None = None
    schedule_source: str | None = None
    season: str | None = None
    game_number: int | None = None


class OutcomeEvidence(BaseModel):
    label: str
    side: Literal["yes", "no"] | None = None
    canonical_participant: str | None = None


class ContractEvidence(CanonicalMarket):
    """Canonical terms plus the typed fields exposed by market-detail MCP results."""

    sports: SportsIdentityEvidence | None = None
    outcome_quotes: tuple[OutcomeEvidence, ...] = ()
    rules_truncated: bool = False
    expected_resolution_time: datetime | None = None
    settlement_value: Decimal | None = None
    winning_outcome: str | None = None
    resolved_at: datetime | None = None


class GameEvidence(BaseModel):
    league: str
    game_ref: str
    source: str
    provider_game_id: str
    home_team: str
    away_team: str
    scheduled_start: datetime
    retrieved_at: datetime
    lifecycle: str


class EventIdentityAssessment(BaseModel):
    verdict: IdentityState
    checks: list[IdentityDimensionCheck]


class ContractEquivalenceAssessment(BaseModel):
    verdict: Verdict
    checks: list[DimensionCheck]


class MarketGameAssessment(BaseModel):
    market_platform: Platform
    market_id: str
    game_ref: str
    game_source: str
    observed_at: datetime
    game_lifecycle: str
    verdict: IdentityState
    checks: list[IdentityDimensionCheck]
    use_together: bool
    scope: str = (
        "Sporting-event identity only; this does not establish contract equivalence, "
        "market settlement, or payout semantics."
    )


class PairAssessment(BaseModel):
    polymarket_id: str
    kalshi_id: str
    verdict: Verdict
    relationship: Literal["primary_candidate", "related_context", "unrelated"]
    checks: list[DimensionCheck]
    review_required: bool
    comparison_allowed: bool
    event_identity: EventIdentityAssessment | None = None
    contract_equivalence: ContractEquivalenceAssessment | None = None
    semantic_review_route: Literal["main_model_explanation", "milestone_8_jev_not_integrated"] = (
        "main_model_explanation"
    )
    scope: str = "Supplied terms only; external or omitted contract terms are not verified."


class MatchingReport(BaseModel):
    pairs: list[PairAssessment] = Field(max_length=9)
    market_to_game: list[MarketGameAssessment] = Field(default_factory=list, max_length=16)
    candidates_per_platform: int = MAX_CANDIDATES
    review_route: str = (
        "The main model may explain unresolved terms but cannot override deterministic rejection."
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
    required: bool = True,
    left_provenance: str | None = None,
    right_provenance: str | None = None,
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
        required=required,
        left_provenance=left_provenance,
        right_provenance=right_provenance,
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


def _assess_generic_pair(
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


def _identity_check(
    dimension: str,
    state: IdentityState,
    reason: str,
    left: object,
    right: object,
    *,
    required: bool = True,
    left_provenance: str,
    right_provenance: str,
) -> IdentityDimensionCheck:
    return IdentityDimensionCheck(
        dimension=dimension,
        state=state,
        reason=reason,
        market_value=str(left)[:300] if left is not None else None,
        comparison_value=str(right)[:300] if right is not None else None,
        market_provenance=left_provenance,
        comparison_provenance=right_provenance,
        required=required,
    )


def _season(identity: SportsIdentityEvidence) -> str | None:
    if identity.season:
        return identity.season
    if identity.scheduled_start is None:
        return None
    year = identity.scheduled_start.year
    if identity.league in {"nfl", "ncaa_football"} and identity.scheduled_start.month <= 2:
        year -= 1
    return str(year)


def _sports_event_identity(
    left: ContractEvidence, right: ContractEvidence
) -> EventIdentityAssessment:
    a, b = left.sports, right.sports
    prefix_a = f"{left.platform.value}.market_detail.sports"
    prefix_b = f"{right.platform.value}.market_detail.sports"
    if a is None or b is None:
        return EventIdentityAssessment(
            verdict="insufficient_evidence",
            checks=[
                _identity_check(
                    "sports_identity",
                    "insufficient_evidence",
                    "Both market details must supply typed sports identity.",
                    "present" if a else None,
                    "present" if b else None,
                    left_provenance=prefix_a,
                    right_provenance=prefix_b,
                )
            ],
        )

    checks: list[IdentityDimensionCheck] = []

    def equality(
        dimension: str,
        value_a: object,
        value_b: object,
        *,
        required: bool = True,
        reason: str | None = None,
    ) -> None:
        if value_a is None or value_b is None:
            state: IdentityState = "insufficient_evidence"
        else:
            state = "match" if value_a == value_b else "different"
        checks.append(
            _identity_check(
                dimension,
                state,
                reason
                or {
                    "match": "Provider-backed values agree.",
                    "different": "Provider-backed values conflict.",
                    "insufficient_evidence": "A required provider-backed value is missing.",
                }[state],
                value_a,
                value_b,
                required=required,
                left_provenance=f"{prefix_a}.{dimension}",
                right_provenance=f"{prefix_b}.{dimension}",
            )
        )

    equality("league", a.league, b.league)
    equality("season", _season(a), _season(b))
    equality("participants", sorted(a.participants), sorted(b.participants))

    internal_a = bool(left.event_id and left.event_id == a.provider_event_id)
    internal_b = bool(right.event_id and right.event_id == b.provider_event_id)
    provider_state: IdentityState = (
        "match"
        if internal_a and internal_b
        else "different"
        if (left.event_id and not internal_a) or (right.event_id and not internal_b)
        else "insufficient_evidence"
    )
    checks.append(
        _identity_check(
            "provider_event_identity",
            provider_state,
            (
                "Each market ID agrees with its own provider event namespace; cross-provider IDs "
                "are not expected to be equal."
                if provider_state == "match"
                else "A market's event ID conflicts with its embedded provider sports identity."
                if provider_state == "different"
                else "Provider event identity is missing."
            ),
            f"{left.event_id} / {a.provider_event_id}",
            f"{right.event_id} / {b.provider_event_id}",
            left_provenance=f"{left.platform.value}.market_detail.event_id",
            right_provenance=f"{right.platform.value}.market_detail.event_id",
        )
    )

    if a.scheduled_start is None or b.scheduled_start is None:
        start_state: IdentityState = "insufficient_evidence"
        start_reason = "Both providers must supply a timezone-aware scheduled start."
    else:
        drift = abs(a.scheduled_start - b.scheduled_start)
        start_state = "match" if drift <= START_TOLERANCE else "different"
        start_reason = (
            f"Scheduled starts differ by {int(drift.total_seconds() // 60)} minutes; "
            f"the allowed provider drift is {int(START_TOLERANCE.total_seconds() // 60)} minutes."
        )
    checks.append(
        _identity_check(
            "scheduled_start",
            start_state,
            start_reason,
            a.scheduled_start,
            b.scheduled_start,
            left_provenance=f"{prefix_a}.scheduled_start ({a.schedule_source or 'unknown source'})",
            right_provenance=(
                f"{prefix_b}.scheduled_start ({b.schedule_source or 'unknown source'})"
            ),
        )
    )
    equality("market_type", a.market_type, b.market_type)

    if a.game_number is None and b.game_number is None:
        game_state: IdentityState = "match"
        game_reason = (
            "Neither provider identifies a game number; participants and start remain decisive."
        )
    elif a.game_number is None or b.game_number is None:
        game_state = "insufficient_evidence"
        game_reason = "Only one provider identifies the game number."
    else:
        game_state = "match" if a.game_number == b.game_number else "different"
        game_reason = "Game numbers agree." if game_state == "match" else "Game numbers conflict."
    checks.append(
        _identity_check(
            "game_number",
            game_state,
            game_reason,
            a.game_number,
            b.game_number,
            required=not (a.game_number is None and b.game_number is None),
            left_provenance=f"{prefix_a}.game_number",
            right_provenance=f"{prefix_b}.game_number",
        )
    )

    required = [check for check in checks if check.required]
    verdict: IdentityState = (
        "different"
        if any(check.state == "different" for check in checks)
        else "match"
        if all(check.state == "match" for check in required)
        else "insufficient_evidence"
    )
    return EventIdentityAssessment(verdict=verdict, checks=checks)


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7}


def _postponement_term(rules: str | None) -> str | None:
    text = normalize(rules)
    match = re.search(
        r"(?:postpon\w*|reschedul\w*)[^.!?]{0,100}?(?:within|up to) "
        r"(?P<amount>\d+|one|two|three|four|five|seven) (?P<unit>hours?|days?)",
        text,
    )
    if match:
        raw = match["amount"]
        amount = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        hours = amount if match["unit"].startswith("hour") else amount * 24
        return f"reschedule_window_hours={hours}"
    if re.search(r"postpon\w*[^.!?]{0,80}(?:do not count|excluded|void)", text):
        return "postponed_game_excluded"
    return None


def _payout_term(rules: str | None, trigger: str) -> str | None:
    text = normalize(rules)
    clause = next(
        (item for item in re.split(r"[.!?]\s+|\n+", text) if re.search(trigger, item)),
        None,
    )
    if clause is None:
        return None
    for pattern, value in (
        (r"50\s*[-/]?\s*50|half(?:way)?", "50_50"),
        (r"fair value", "fair_value"),
        (r"\bvoid\b|refund", "void"),
        (r"resolves?(?: to)? yes", "yes"),
        (r"resolves?(?: to)? no", "no"),
        (r"official (?:result|game).*(?:counts?|used)", "official_result"),
    ):
        if re.search(pattern, clause):
            return value
    return None


def _inclusion_term(rules: str | None, subject: str) -> str | None:
    text = normalize(rules)
    clause = next(
        (item for item in re.split(r"[.!?]\s+|\n+", text) if re.search(subject, item)),
        None,
    )
    if clause is None:
        return None
    if re.search(r"does not count|not included|excluded|void", clause):
        return "excluded"
    if re.search(r"included|counts?|official (?:result|game)", clause):
        return "included"
    return None


def _official_result_term(rules: str | None) -> str | None:
    text = normalize(rules)
    if re.search(r"preliminary (?:result|winner)", text):
        return "preliminary_result"
    if re.search(r"official (?:league |governing body )?(?:result|winner|game)", text):
        return "official_result"
    return None


def _sports_term_check(
    dimension: str,
    left: object,
    right: object,
    *,
    left_market: ContractEvidence,
    right_market: ContractEvidence,
    mismatch: CheckState = "different",
    provenance_field: str = "rules",
) -> DimensionCheck:
    missing = left is None or right is None
    return _check(
        dimension,
        left,
        right,
        missing=missing,
        mismatch=mismatch,
        reason=(
            "Required settlement evidence is missing or unsupported."
            if missing
            else "Explicit supplied settlement terms agree."
            if left == right
            else "Explicit supplied settlement terms conflict."
            if mismatch == "different"
            else "Supplied wording differs and requires semantic review."
        ),
        left_provenance=f"{left_market.platform.value}.market_detail.{provenance_field}",
        right_provenance=f"{right_market.platform.value}.market_detail.{provenance_field}",
    )


def _outcome_mapping_check(left: ContractEvidence, right: ContractEvidence) -> DimensionCheck:
    left_named = {
        quote.canonical_participant for quote in left.outcome_quotes if quote.canonical_participant
    }
    right_yes = {
        quote.canonical_participant
        for quote in right.outcome_quotes
        if quote.side == "yes" and quote.canonical_participant
    }
    expected = set(left.sports.participants) if left.sports else set()
    if not left_named or len(right_yes) != 1 or not expected:
        return _sports_term_check(
            "named_outcome_mapping",
            sorted(left_named) or None,
            sorted(right_yes) or None,
            left_market=left,
            right_market=right,
        )
    target = next(iter(right_yes))
    if left_named == expected and target in expected:
        state: CheckState = "match"
    elif left_named - expected or target not in expected:
        state = "different"
    else:
        state = "unknown"
    return DimensionCheck(
        dimension="named_outcome_mapping",
        state=state,
        reason=(
            "Kalshi YES maps to the same named participant as a Polymarket outcome. Kalshi NO is "
            "not inferred to equal the opponent; settlement checks remain required."
            if state == "match"
            else "Kalshi YES names a participant absent from the Polymarket event."
            if state == "different"
            else "Polymarket does not supply a complete named-outcome mapping."
        ),
        left=str(sorted(left_named)),
        right=target,
        left_provenance="polymarket.market_detail.outcome_quotes.canonical_participant",
        right_provenance="kalshi.market_detail.outcome_quotes[side=yes].canonical_participant",
    )


def _sports_contract_equivalence(
    left: ContractEvidence,
    right: ContractEvidence,
    event_identity: EventIdentityAssessment,
) -> ContractEquivalenceAssessment:
    complete = bool(left.rules and right.rules) and not (
        left.rules_truncated or right.rules_truncated
    )
    checks = [_outcome_mapping_check(left, right)]
    checks.append(
        _sports_term_check(
            "line_or_threshold",
            left.sports.line if left.sports else None,
            right.sports.line if right.sports else None,
            left_market=left,
            right_market=right,
        )
    )
    if left.sports and right.sports and left.sports.line is None and right.sports.line is None:
        checks[-1] = _check(
            "line_or_threshold",
            "none (full-game winner)",
            "none (full-game winner)",
            left_provenance="polymarket.market_detail.sports.line",
            right_provenance="kalshi.market_detail.sports.line",
        )

    checks.extend(
        [
            _sports_term_check(
                "resolution_authority",
                normalize(left.resolution_source) or None,
                normalize(right.resolution_source) or None,
                left_market=left,
                right_market=right,
                provenance_field="resolution_source",
            ),
            _sports_term_check(
                "official_result_requirement",
                _official_result_term(left.rules),
                _official_result_term(right.rules),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "postponement_window",
                _postponement_term(left.rules),
                _postponement_term(right.rules),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "cancellation_payout",
                _payout_term(left.rules, r"cancel"),
                _payout_term(right.rules, r"cancel"),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "overtime_or_extra_innings",
                _inclusion_term(left.rules, r"overtime|extra innings"),
                _inclusion_term(right.rules, r"overtime|extra innings"),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "tie_treatment",
                _payout_term(left.rules, r"\btie\b|\bdraw\b"),
                _payout_term(right.rules, r"\btie\b|\bdraw\b"),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "shortened_game_treatment",
                _payout_term(left.rules, r"shorten|minimum .{0,20}innings|official game"),
                _payout_term(right.rules, r"shorten|minimum .{0,20}innings|official game"),
                left_market=left,
                right_market=right,
            ),
            _sports_term_check(
                "abandoned_game_treatment",
                _payout_term(left.rules, r"abandon"),
                _payout_term(right.rules, r"abandon"),
                left_market=left,
                right_market=right,
            ),
        ]
    )

    exclusions_a = _clauses(left.rules, r"except|exclud") if complete else ()
    exclusions_b = _clauses(right.rules, r"except|exclud") if complete else ()
    checks.append(
        _sports_term_check(
            "material_exclusions",
            exclusions_a if exclusions_a else "none supplied" if complete else None,
            exclusions_b if exclusions_b else "none supplied" if complete else None,
            left_market=left,
            right_market=right,
            mismatch="unknown",
        )
    )
    checks.append(
        _sports_term_check(
            "full_rules",
            normalize(left.rules) if complete else None,
            normalize(right.rules) if complete else None,
            left_market=left,
            right_market=right,
            mismatch="unknown",
        )
    )
    for dimension, value_a, value_b, reason in (
        (
            "trading_close",
            left.close_time,
            right.close_time,
            "Trading close is a separate clock and does not identify the sporting event.",
        ),
        (
            "expected_resolution_time",
            left.expected_resolution_time,
            right.expected_resolution_time,
            "Expected resolution is a separate operational clock.",
        ),
        (
            "resolution_deadline",
            left.resolution_deadline,
            right.resolution_deadline,
            "Final resolution deadline is separate from scheduled start.",
        ),
    ):
        checks.append(
            _check(
                dimension,
                value_a,
                value_b,
                missing=value_a is None or value_b is None or value_a != value_b,
                mismatch="unknown",
                reason=reason,
                required=False,
                left_provenance=f"{left.platform.value}.market_detail.{dimension}",
                right_provenance=f"{right.platform.value}.market_detail.{dimension}",
            )
        )

    rejected = any(check.state == "different" for check in checks)
    required = [check for check in checks if check.required]
    equivalent = (
        event_identity.verdict == "match"
        and not rejected
        and complete
        and all(check.state == "match" for check in required)
    )
    verdict: Verdict = "different" if rejected else "equivalent" if equivalent else "ambiguous"
    return ContractEquivalenceAssessment(verdict=verdict, checks=checks)


def _assess_sports_pair(left: ContractEvidence, right: ContractEvidence) -> PairAssessment:
    event = _sports_event_identity(left, right)
    contract = _sports_contract_equivalence(left, right, event)
    if event.verdict == "different" or contract.verdict == "different":
        verdict: Verdict = "different"
    elif event.verdict == "match" and contract.verdict == "equivalent":
        verdict = "equivalent"
    else:
        verdict = "ambiguous"
    summary_state: CheckState = (
        "match"
        if event.verdict == "match"
        else "different"
        if event.verdict == "different"
        else "unknown"
    )
    checks = [
        DimensionCheck(
            dimension="event_identity",
            state=summary_state,
            reason=f"Typed sports event identity verdict: {event.verdict}.",
            left=left.event_id,
            right=right.event_id,
            left_provenance="polymarket.market_detail.sports",
            right_provenance="kalshi.market_detail.sports",
        ),
        *contract.checks,
    ]
    return PairAssessment(
        polymarket_id=left.market_id,
        kalshi_id=right.market_id,
        verdict=verdict,
        relationship=(
            "primary_candidate"
            if verdict == "equivalent"
            else "unrelated"
            if event.verdict == "different"
            else "related_context"
        ),
        checks=checks,
        review_required=verdict == "ambiguous",
        comparison_allowed=verdict == "equivalent",
        event_identity=event,
        contract_equivalence=contract,
        semantic_review_route="milestone_8_jev_not_integrated",
        scope=(
            "Supplied full-game-winner identity, named outcomes, and settlement terms only; "
            "sports-state evidence cannot alter this verdict."
        ),
    )


def _contract_evidence(market: CanonicalMarket | ContractEvidence) -> ContractEvidence:
    if isinstance(market, ContractEvidence):
        return market
    return ContractEvidence.model_validate(market.model_dump())


def assess_pair(
    polymarket: CanonicalMarket | ContractEvidence,
    kalshi: CanonicalMarket | ContractEvidence,
    *,
    polymarket_truncated: bool = False,
    kalshi_truncated: bool = False,
) -> PairAssessment:
    left, right = _contract_evidence(polymarket), _contract_evidence(kalshi)
    if left.platform != Platform.POLYMARKET or right.platform != Platform.KALSHI:
        raise ValueError("Expected one Polymarket and one Kalshi contract")
    if left.sports is not None or right.sports is not None:
        left = left.model_copy(update={"rules_truncated": polymarket_truncated})
        right = right.model_copy(update={"rules_truncated": kalshi_truncated})
        return _assess_sports_pair(left, right)
    return _assess_generic_pair(
        left,
        right,
        polymarket_truncated=polymarket_truncated,
        kalshi_truncated=kalshi_truncated,
    )


def assess_market_game(
    market: CanonicalMarket | ContractEvidence, game: GameEvidence
) -> MarketGameAssessment:
    contract = _contract_evidence(market)
    sports = contract.sports
    prefix = f"{contract.platform.value}.market_detail.sports"
    game_prefix = f"sports_state.{game.source}.game_state"
    if sports is None:
        checks = [
            _identity_check(
                "sports_identity",
                "insufficient_evidence",
                "The market detail has no typed sports identity.",
                None,
                game.game_ref,
                left_provenance=prefix,
                right_provenance=f"{game_prefix}.game_ref",
            )
        ]
    else:
        checks = []
        for dimension, market_value, game_value in (
            ("league", sports.league, game.league),
            ("participants", sorted(sports.participants), sorted((game.home_team, game.away_team))),
        ):
            state: IdentityState = "match" if market_value == game_value else "different"
            checks.append(
                _identity_check(
                    dimension,
                    state,
                    "Provider-backed values agree."
                    if state == "match"
                    else "Market and sports-state identities conflict.",
                    market_value,
                    game_value,
                    left_provenance=f"{prefix}.{dimension}",
                    right_provenance=f"{game_prefix}.{dimension}",
                )
            )
        if sports.scheduled_start is None:
            start_state: IdentityState = "insufficient_evidence"
            start_reason = "The market provider supplies no scheduled start."
        else:
            drift = abs(sports.scheduled_start - game.scheduled_start)
            start_state = "match" if drift <= START_TOLERANCE else "different"
            start_reason = (
                f"Scheduled starts differ by {int(drift.total_seconds() // 60)} minutes; "
                "the allowed provider drift is "
                f"{int(START_TOLERANCE.total_seconds() // 60)} minutes."
            )
        checks.append(
            _identity_check(
                "scheduled_start",
                start_state,
                start_reason,
                sports.scheduled_start,
                game.scheduled_start,
                left_provenance=f"{prefix}.scheduled_start",
                right_provenance=f"{game_prefix}.scheduled_start",
            )
        )
        ref_state: IdentityState = (
            "match"
            if sports.provider_event_id and game.game_ref and game.provider_game_id
            else "insufficient_evidence"
        )
        checks.append(
            _identity_check(
                "provider_backed_game_reference",
                ref_state,
                (
                    "Both observations carry provider-backed identifiers in separate namespaces."
                    if ref_state == "match"
                    else "A provider-backed game identifier is missing."
                ),
                sports.provider_event_id,
                f"{game.source}:{game.provider_game_id}",
                left_provenance=f"{prefix}.provider_event_id",
                right_provenance=f"{game_prefix}.provider_game_id",
            )
        )
    verdict: IdentityState = (
        "different"
        if any(check.state == "different" for check in checks)
        else "match"
        if all(check.state == "match" for check in checks if check.required)
        else "insufficient_evidence"
    )
    return MarketGameAssessment(
        market_platform=contract.platform,
        market_id=contract.market_id,
        game_ref=game.game_ref,
        game_source=game.source,
        observed_at=game.retrieved_at,
        game_lifecycle=game.lifecycle,
        verdict=verdict,
        checks=checks,
        use_together=verdict == "match",
    )


def match_candidates(
    polymarket: list[CanonicalMarket | ContractEvidence],
    kalshi: list[CanonicalMarket | ContractEvidence],
    *,
    truncated: set[tuple[Platform, str]] | None = None,
    games: list[GameEvidence] | None = None,
) -> MatchingReport:
    """At most three distinct candidates per platform and nine pairs; no hidden API calls."""

    def bounded(
        markets: list[CanonicalMarket | ContractEvidence],
    ) -> list[CanonicalMarket | ContractEvidence]:
        unique: dict[str, CanonicalMarket | ContractEvidence] = {}
        for market in markets:
            unique.setdefault(market.market_id, market)
            if len(unique) == MAX_CANDIDATES:
                break
        return list(unique.values())

    truncated = truncated or set()
    left = bounded(polymarket)
    right = bounded(kalshi)
    markets = [*left, *right]
    return MatchingReport(
        pairs=[
            assess_pair(
                a,
                b,
                polymarket_truncated=(a.platform, a.market_id) in truncated,
                kalshi_truncated=(b.platform, b.market_id) in truncated,
            )
            for a, b in product(left, right)
        ],
        market_to_game=[
            assess_market_game(market, game) for market, game in product(markets, games or [])
        ],
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
    for assessment in report.market_to_game:
        finding = {
            "match": "Identity matches; sporting state may be used only as corroborating context.",
            "different": "Identity conflict; do not combine these market and game observations.",
            "insufficient_evidence": (
                "Identity is not established; do not combine these observations."
            ),
        }[assessment.verdict]
        lines.append(
            f"- Market/game check ({assessment.market_platform.value} "
            f"{assessment.market_id} / {assessment.game_source}): {finding}"
        )
    return "\n".join(lines)
