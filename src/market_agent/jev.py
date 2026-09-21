"""Bounded Jev review for deterministic sports-contract ambiguity."""

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_agent.domain import Platform
from market_agent.domain.matching import (
    ContractEvidence,
    MatchingReport,
    PairAssessment,
    SemanticReview,
    Verdict,
    VerdictProbabilities,
)

JEV_GATEWAY_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
JEV_MODEL = "typesafe-ai/jev"
MAX_REVIEW_PAIRS = 3
MAX_RULE_CHARS = 12_000
MAX_RESPONSE_BYTES = 1_000_000
RETRYABLE_STATUSES = {429, 500, 502, 503, 504, 529}

EQUIVALENCE_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": (
        "Classify whether buying the named team outcome in these two full-game winner contracts "
        "has identical payout semantics. Use only the supplied evidence and review every "
        "listed dimension. Check official-result authority, postponement and "
        "cancellation, overtime, ties, shortened and abandoned games, and exclusions. Never "
        "infer an omitted rule or use prices, scores, or game results."
    ),
    "criteria": {
        "equivalent": (
            "The supplied complete terms establish the same named outcome and payout in every "
            "material scenario. Wording may differ, but omissions do not count as agreement."
        ),
        "different": (
            "At least one explicit supplied scenario can produce a different payout or winner."
        ),
        "ambiguous": (
            "No explicit conflict is established, but a required term is missing, unclear, or "
            "unsupported by the supplied evidence."
        ),
    },
}


class SemanticReviewer(Protocol):
    async def review(
        self, report: MatchingReport, contracts: list[ContractEvidence]
    ) -> MatchingReport: ...


class JevReviewError(RuntimeError):
    """Safe Jev failure that never contains credentials or full provider bodies."""


class _GatewayChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    choice: Verdict
    probabilities: VerdictProbabilities


class _GatewayUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    input_tokens: int = Field(alias="inputTokens", ge=0)


class _TypeSafeMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    confidence: dict[str, float]


class _ProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    typesafe: _TypeSafeMetadata


class _GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: dict[str, _GatewayChoice]
    usage: _GatewayUsage
    provider_metadata: _ProviderMetadata = Field(alias="providerMetadata")


def _contract_key(contract: ContractEvidence) -> tuple[Platform, str]:
    return contract.platform, contract.market_id


def _review_state(
    left: ContractEvidence,
    right: ContractEvidence,
    pair: PairAssessment,
    *,
    force_review: bool,
) -> dict[str, Any]:
    assert left.sports is not None and right.sports is not None
    assert pair.event_identity is not None and pair.event_identity.verdict == "match"
    review_checks = [
        check
        for check in pair.checks
        if check.required and (force_review or check.state == "unknown")
    ]
    matched = [
        check.dimension for check in pair.checks if check.required and check.state == "match"
    ]
    return {
        "task": "sports_contract_equivalence",
        "review_mode": "forced" if force_review else "ambiguity_only",
        "policy": (
            "Classify supplied contract meaning only. Sporting results and market prices are not "
            "evidence of contract equivalence or settlement."
        ),
        "event_identity": {
            "deterministic_verdict": "match",
            "league": left.sports.league,
            "season": left.sports.season,
            "participants": sorted(left.sports.participants),
            "market_type": left.sports.market_type,
            "line": str(left.sports.line) if left.sports.line is not None else None,
            "game_number": left.sports.game_number,
        },
        "named_outcome_mapping": {
            "deterministic_verdict": "match",
            "polymarket": sorted(
                quote.canonical_participant
                for quote in left.outcome_quotes
                if quote.canonical_participant
            ),
            "kalshi_yes": next(
                (
                    quote.canonical_participant
                    for quote in right.outcome_quotes
                    if quote.side == "yes" and quote.canonical_participant
                ),
                None,
            ),
        },
        "deterministically_matched_dimensions": matched,
        "dimensions_to_review": [
            {
                "dimension": check.dimension,
                "polymarket": check.left,
                "kalshi": check.right,
                "deterministic_state": check.state,
            }
            for check in review_checks
        ],
        "contracts": {
            "polymarket": {
                "rules": left.rules,
                "resolution_authority": left.resolution_source,
            },
            "kalshi": {
                "rules": right.rules,
                "resolution_authority": right.resolution_source,
            },
        },
    }


def _eligible(
    left: ContractEvidence | None,
    right: ContractEvidence | None,
    pair: PairAssessment,
    *,
    force_review: bool,
) -> bool:
    common_requirements = bool(
        left is not None
        and right is not None
        and left.sports is not None
        and right.sports is not None
        and pair.event_identity is not None
        and pair.event_identity.verdict == "match"
        and pair.contract_equivalence is not None
        and left.rules
        and right.rules
        and not left.rules_truncated
        and not right.rules_truncated
        and len(left.rules) <= MAX_RULE_CHARS
        and len(right.rules) <= MAX_RULE_CHARS
    )
    if force_review:
        return common_requirements
    return bool(
        common_requirements
        and pair.verdict == "ambiguous"
        and pair.contract_equivalence is not None
        and pair.contract_equivalence.verdict == "ambiguous"
        and not any(check.state == "different" for check in pair.checks)
    )


def _apply_review(
    pair: PairAssessment,
    review: SemanticReview,
    *,
    equivalent_threshold: float,
    different_threshold: float,
    confidence_threshold: float,
    force_review: bool,
) -> PairAssessment:
    threshold = equivalent_threshold if review.verdict == "equivalent" else different_threshold
    accepted = (
        review.verdict != "ambiguous"
        and review.selected_probability >= threshold
        and review.confidence >= confidence_threshold
    )
    if not accepted:
        update: dict[str, Any] = {
            "semantic_review": review,
            "semantic_review_route": "main_model_fallback",
            "semantic_review_note": (
                "Forced Jev review did not clear the configured action threshold; the final "
                "verdict is ambiguous."
                if force_review
                else "Jev returned a bounded decision below the configured action threshold; "
                "deterministic ambiguity is retained."
            ),
        }
        if force_review:
            update.update(
                verdict="ambiguous",
                relationship="related_context",
                review_required=True,
                comparison_allowed=False,
            )
        return pair.model_copy(update=update)
    verdict = review.verdict
    return pair.model_copy(
        update={
            "verdict": verdict,
            "relationship": "primary_candidate" if verdict == "equivalent" else "related_context",
            "review_required": False,
            "comparison_allowed": verdict == "equivalent",
            "semantic_review": review,
            "semantic_review_route": "jev",
            "semantic_review_note": (
                "Experimental forced review used Jev as the final settlement-semantics "
                "classifier after deterministic event identity matched."
                if force_review
                else "Jev resolved only semantic ambiguity that remained after deterministic "
                "identity and conflict checks."
            ),
        }
    )


class JevReviewer:
    """Review eligible ambiguous pairs through the Jev-only AI Gateway endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 3,
        equivalent_threshold: float = 0.9,
        different_threshold: float = 0.75,
        confidence_threshold: float = 0.6,
        force_review: bool = False,
        retry_backoff_seconds: float = 0.1,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Jev API key cannot be empty")
        if timeout_seconds <= 0 or retry_backoff_seconds < 0:
            raise ValueError("Jev timeout must be positive and backoff cannot be negative")
        for value in (equivalent_threshold, different_threshold, confidence_threshold):
            if not 0 <= value <= 1:
                raise ValueError("Jev thresholds must be between zero and one")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.equivalent_threshold = equivalent_threshold
        self.different_threshold = different_threshold
        self.confidence_threshold = confidence_threshold
        self.force_review = force_review
        self.retry_backoff_seconds = retry_backoff_seconds
        self._cache: OrderedDict[str, SemanticReview] = OrderedDict()
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Ai-Gateway-Protocol-Version": "0.0.1",
            "Ai-Gateway-Auth-Method": "api-key",
            "Ai-Evaluation-Model-Specification-Version": "4",
            "Ai-Model-Id": JEV_MODEL,
            "Content-Type": "application/json",
            "User-Agent": "cross-market-agent/0.1",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _call(self, state: Mapping[str, Any], dimensions: list[str]) -> SemanticReview:
        body = {"state": state, "questions": {"equivalence": EQUIVALENCE_QUESTION}}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        cache_key = hashlib.sha256(encoded).hexdigest()
        if cached := self._cache.get(cache_key):
            self._cache.move_to_end(cache_key)
            return cached.model_copy(update={"cached": True})

        started = time.perf_counter()
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                response = await self._http.post(
                    JEV_GATEWAY_URL,
                    headers=self._headers,
                    content=encoded,
                    timeout=self.timeout,
                )
            except httpx.TransportError as error:
                if attempt == 0:
                    await asyncio.sleep(self.retry_backoff_seconds)
                    continue
                raise JevReviewError("Jev transport failed after two attempts") from error
            if response.status_code in RETRYABLE_STATUSES and attempt == 0:
                await asyncio.sleep(self.retry_backoff_seconds)
                continue
            break
        if response is None:
            raise JevReviewError("Jev returned no response")
        if response.status_code != 200:
            raise JevReviewError(f"Jev request failed with HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise JevReviewError("Jev response exceeded the size limit")
        try:
            payload = _GatewayResponse.model_validate_json(response.content)
            answer = payload.answers["equivalence"]
            if answer.type != "choice":
                raise ValueError("unexpected Jev answer type")
            confidence = payload.provider_metadata.typesafe.confidence["equivalence"]
            if not 0 <= confidence <= 1:
                raise ValueError("invalid Jev confidence")
            selected_probability = getattr(answer.probabilities, answer.choice)
        except (KeyError, ValueError, ValidationError) as error:
            raise JevReviewError("Jev response failed schema validation") from error

        review = SemanticReview(
            verdict=answer.choice,
            probabilities=answer.probabilities,
            confidence=confidence,
            selected_probability=selected_probability,
            reviewed_dimensions=dimensions[:12],
            input_tokens=payload.usage.input_tokens,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )
        self._cache[cache_key] = review
        self._cache.move_to_end(cache_key)
        while len(self._cache) > 128:
            self._cache.popitem(last=False)
        return review

    async def review(
        self, report: MatchingReport, contracts: list[ContractEvidence]
    ) -> MatchingReport:
        by_key = {_contract_key(contract): contract for contract in contracts}
        work: list[tuple[int, PairAssessment, ContractEvidence, ContractEvidence]] = []
        pairs = list(report.pairs)
        for index, pair in enumerate(pairs):
            left = by_key.get((Platform.POLYMARKET, pair.polymarket_id))
            right = by_key.get((Platform.KALSHI, pair.kalshi_id))
            if _eligible(left, right, pair, force_review=self.force_review):
                assert left is not None and right is not None
                if len(work) < MAX_REVIEW_PAIRS:
                    work.append((index, pair, left, right))
                else:
                    pairs[index] = pair.model_copy(
                        update={
                            "semantic_review_note": (
                                "Jev review skipped because the three-pair request limit was "
                                "reached."
                            )
                        }
                    )
            elif pair.verdict == "ambiguous" or (
                self.force_review
                and pair.event_identity is not None
                and pair.event_identity.verdict == "match"
            ):
                pairs[index] = pair.model_copy(
                    update={
                        "semantic_review_note": (
                            "Jev review requires matched typed event identity and complete, "
                            "untruncated rules."
                        )
                    }
                )

        calls = [
            self._call(
                _review_state(left, right, pair, force_review=self.force_review),
                [
                    check.dimension
                    for check in pair.checks
                    if check.required and (self.force_review or check.state == "unknown")
                ],
            )
            for _, pair, left, right in work
        ]
        results = await asyncio.gather(*calls, return_exceptions=True)
        for (index, pair, _, _), result in zip(work, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                update: dict[str, Any] = {
                    "semantic_review_note": (
                        "Forced Jev review was unavailable after bounded retry; the final verdict "
                        "is ambiguous."
                        if self.force_review
                        else "Jev was unavailable after bounded retry; deterministic ambiguity is "
                        "retained."
                    )
                }
                if self.force_review:
                    update.update(
                        verdict="ambiguous",
                        relationship="related_context",
                        review_required=True,
                        comparison_allowed=False,
                        semantic_review_route="main_model_fallback",
                    )
                pairs[index] = pair.model_copy(update=update)
                continue
            pairs[index] = _apply_review(
                pair,
                result,
                equivalent_threshold=self.equivalent_threshold,
                different_threshold=self.different_threshold,
                confidence_threshold=self.confidence_threshold,
                force_review=self.force_review,
            )
        return report.model_copy(
            update={
                "pairs": pairs,
                "review_route": (
                    "Experimental forced mode sends at most three complete same-event sports "
                    "pairs to Jev and uses a thresholded Jev decision for settlement semantics. "
                    "Different events and incomplete evidence remain deterministic safety stops."
                    if self.force_review
                    else "Jev reviews at most three complete ambiguous sports pairs. "
                    "Deterministic conflicts veto review; low-confidence or unavailable results "
                    "retain ambiguity for main-model explanation."
                ),
            }
        )
