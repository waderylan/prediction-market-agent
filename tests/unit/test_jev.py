import json

import httpx
import pytest
from jev_cases import contracts as evaluation_contracts
from jev_cases import load_cases

from market_agent.domain import Platform
from market_agent.domain.matching import ContractEvidence, match_candidates
from market_agent.jev import JEV_GATEWAY_URL, JEV_MODEL, JevReviewer

pytestmark = pytest.mark.unit

LEFT_RULES = (
    "Winner is the official NFL winner and includes overtime. If postponed, the market stays "
    "open until completion. A cancellation or tie pays each team contract $0.50."
)
RIGHT_RULES = (
    "The official league result after overtime determines the winner. Delayed or postponed games "
    "remain open until played. If cancelled or drawn, both sides settle at 50-50."
)


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["name"])
def test_labeled_evaluation_case_has_expected_deterministic_verdict(case):
    left, right = evaluation_contracts(case)
    truncated = {(Platform.KALSHI, right.market_id)} if case.get("right_truncated") else set()
    pair = match_candidates([left], [right], truncated=truncated).pairs[0]

    assert pair.verdict == case["expected_deterministic"]


def market(platform: str, market_id: str, *, rules: str | None = None) -> ContractEvidence:
    is_poly = platform == "polymarket"
    return ContractEvidence.model_validate(
        {
            "platform": platform,
            "market_id": market_id,
            "event_id": "poly-event" if is_poly else "kalshi-event",
            "title": "Panthers vs Falcons" if is_poly else "Panthers win",
            "outcomes": ["Carolina Panthers", "Atlanta Falcons"] if is_poly else ["Yes", "No"],
            "status": "open",
            "rules": rules if rules is not None else (LEFT_RULES if is_poly else RIGHT_RULES),
            "resolution_source": "https://www.nfl.com/scores",
            "source_url": "https://example.test/market",
            "retrieved_at": "2026-09-20T16:00:00Z",
            "sports": {
                "league": "nfl",
                "provider_event_id": "poly-event" if is_poly else "kalshi-event",
                "participants": ["Carolina Panthers", "Atlanta Falcons"],
                "market_type": "game_winner",
                "scheduled_start": "2026-09-20T17:00:00Z",
                "season": "2026",
            },
            "outcome_quotes": (
                [
                    {
                        "label": "Carolina Panthers",
                        "canonical_participant": "Carolina Panthers",
                    },
                    {
                        "label": "Atlanta Falcons",
                        "canonical_participant": "Atlanta Falcons",
                    },
                ]
                if is_poly
                else [
                    {
                        "label": "Carolina Panthers",
                        "side": "yes",
                        "canonical_participant": "Carolina Panthers",
                    },
                    {"label": "Not Carolina Panthers", "side": "no"},
                ]
            ),
        }
    )


def response(
    verdict: str = "equivalent",
    *,
    probabilities: dict[str, float] | None = None,
    confidence: float = 0.8,
) -> httpx.Response:
    probabilities = probabilities or {
        "equivalent": 0.94,
        "different": 0.01,
        "ambiguous": 0.05,
    }
    return httpx.Response(
        200,
        json={
            "answers": {
                "equivalence": {
                    "type": "choice",
                    "choice": verdict,
                    "probabilities": probabilities,
                }
            },
            "usage": {"inputTokens": 640, "outputTokens": 20},
            "providerMetadata": {"typesafe": {"confidence": {"equivalence": confidence}}},
        },
    )


def keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for nested in value.values() for key in keys(nested)}
    if isinstance(value, list):
        return {key for nested in value for key in keys(nested)}
    return set()


async def test_review_promotes_high_confidence_equivalence_and_caches_exact_state():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", http_client=http)
        contracts = [market("polymarket", "p1"), market("kalshi", "k1")]
        deterministic = match_candidates(contracts[:1], contracts[1:])

        reviewed = await reviewer.review(deterministic, contracts)
        cached = await reviewer.review(deterministic, contracts)

    pair = reviewed.pairs[0]
    assert deterministic.pairs[0].verdict == "ambiguous"
    assert pair.verdict == "equivalent"
    assert pair.comparison_allowed and not pair.review_required
    assert pair.contract_equivalence is not None
    assert pair.contract_equivalence.verdict == "ambiguous"
    assert pair.semantic_review is not None
    assert pair.semantic_review.probabilities.equivalent == 0.94
    assert cached.pairs[0].semantic_review is not None
    assert cached.pairs[0].semantic_review.cached
    assert len(requests) == 1
    assert requests[0].url == JEV_GATEWAY_URL
    assert requests[0].headers["ai-model-id"] == JEV_MODEL
    body = json.loads(requests[0].content)
    state_text = json.dumps(body["state"])
    assert body["questions"]["equivalence"]["type"] == "choice"
    assert "rules" in state_text and "outcome_quotes" not in state_text
    assert "yes_price" not in state_text and "settlement_value" not in state_text
    assert {"game_state", "score", "settlement_value", "winning_outcome"}.isdisjoint(
        keys(body["state"])
    )


async def test_deterministic_conflict_vetoes_jev():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response()

    left_conflict = "If the game is cancelled, the market resolves 50-50."
    right_conflict = (
        "The official league result determines the winner. If the game is cancelled, "
        "the market resolves Yes."
    )
    contracts = [
        market("polymarket", "p1", rules=left_conflict),
        market("kalshi", "k1", rules=right_conflict),
    ]
    deterministic = match_candidates(contracts[:1], contracts[1:])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewed = await JevReviewer("test-key", http_client=http).review(deterministic, contracts)

    assert deterministic.pairs[0].verdict == "different"
    assert reviewed.pairs[0].verdict == "different"
    assert reviewed.pairs[0].semantic_review is None
    assert calls == 0


async def test_force_review_calls_jev_and_can_override_deterministic_conflict():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response()

    contracts = [
        market(
            "polymarket",
            "p1",
            rules="If the game is cancelled, the market resolves 50-50.",
        ),
        market(
            "kalshi",
            "k1",
            rules="If the game is cancelled, the market resolves Yes.",
        ),
    ]
    deterministic = match_candidates(contracts[:1], contracts[1:])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", force_review=True, http_client=http)
        reviewed = await reviewer.review(deterministic, contracts)

    pair = reviewed.pairs[0]
    assert deterministic.pairs[0].verdict == "different"
    assert pair.verdict == "equivalent"
    assert pair.comparison_allowed and not pair.review_required
    assert pair.semantic_review is not None
    assert pair.semantic_review_route == "jev"
    assert len(requests) == 1
    state = json.loads(requests[0].content)["state"]
    assert state["review_mode"] == "forced"
    assert any(
        dimension["deterministic_state"] == "different"
        for dimension in state["dimensions_to_review"]
    )


async def test_force_review_failure_does_not_fall_back_to_deterministic_settlement_verdict():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    contracts = [
        market("polymarket", "p1", rules="A cancellation resolves 50-50."),
        market("kalshi", "k1", rules="A cancellation resolves Yes."),
    ]
    deterministic = match_candidates(contracts[:1], contracts[1:])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer(
            "test-key", force_review=True, retry_backoff_seconds=0, http_client=http
        )
        reviewed = await reviewer.review(deterministic, contracts)

    pair = reviewed.pairs[0]
    assert deterministic.pairs[0].verdict == "different"
    assert pair.verdict == "ambiguous"
    assert pair.review_required and not pair.comparison_allowed
    assert pair.semantic_review_route == "main_model_fallback"
    assert calls == 2


async def test_low_confidence_result_retains_ambiguity():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: response(
                "different",
                probabilities={"equivalent": 0.03, "different": 0.87, "ambiguous": 0.1},
                confidence=0.4,
            )
        )
    ) as http:
        reviewer = JevReviewer("test-key", http_client=http)
        contracts = [market("polymarket", "p1"), market("kalshi", "k1")]
        reviewed = await reviewer.review(match_candidates(contracts[:1], contracts[1:]), contracts)

    pair = reviewed.pairs[0]
    assert pair.verdict == "ambiguous"
    assert pair.review_required and not pair.comparison_allowed
    assert pair.semantic_review is not None
    assert pair.semantic_review.verdict == "different"
    assert pair.semantic_review_route == "main_model_fallback"
    assert "below" in (pair.semantic_review_note or "")


async def test_retry_then_unavailable_retains_ambiguity():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "sensitive diagnostic"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", retry_backoff_seconds=0, http_client=http)
        contracts = [market("polymarket", "p1"), market("kalshi", "k1")]
        reviewed = await reviewer.review(match_candidates(contracts[:1], contracts[1:]), contracts)

    pair = reviewed.pairs[0]
    assert calls == 2
    assert pair.verdict == "ambiguous"
    assert pair.semantic_review is None
    assert "unavailable" in (pair.semantic_review_note or "")


@pytest.mark.parametrize("mode", ["transport", "malformed"])
async def test_transport_and_malformed_responses_fall_back_without_leaking(mode):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if mode == "transport":
            raise httpx.ConnectError("sensitive diagnostic", request=request)
        return httpx.Response(200, json={"answers": {"equivalence": {"choice": "different"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", retry_backoff_seconds=0, http_client=http)
        pair_contracts = [market("polymarket", "p1"), market("kalshi", "k1")]
        reviewed = await reviewer.review(
            match_candidates(pair_contracts[:1], pair_contracts[1:]), pair_contracts
        )

    pair = reviewed.pairs[0]
    assert calls == (2 if mode == "transport" else 1)
    assert pair.verdict == "ambiguous"
    assert pair.semantic_review is None
    assert pair.semantic_review_note is not None
    assert "sensitive" not in pair.semantic_review_note


async def test_missing_rules_skip_jev_and_fourth_eligible_pair_is_bounded():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(
            "ambiguous", probabilities={"equivalent": 0.1, "different": 0.1, "ambiguous": 0.8}
        )

    polys = [market("polymarket", f"p{i}", rules=f"{LEFT_RULES} Reference P{i}.") for i in range(2)]
    kalshis = [market("kalshi", f"k{i}", rules=f"{RIGHT_RULES} Reference K{i}.") for i in range(2)]
    contracts = [*polys, *kalshis]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reviewer = JevReviewer("test-key", http_client=http)
        reviewed = await reviewer.review(match_candidates(polys, kalshis), contracts)
        missing = [market("polymarket", "pm", rules=""), market("kalshi", "km")]
        skipped = await reviewer.review(match_candidates(missing[:1], missing[1:]), missing)

    assert calls == 3
    assert sum(pair.semantic_review is not None for pair in reviewed.pairs) == 3
    assert "three-pair" in (reviewed.pairs[3].semantic_review_note or "")
    assert skipped.pairs[0].semantic_review is None
    assert "complete" in (skipped.pairs[0].semantic_review_note or "")
