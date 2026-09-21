"""Opt-in Jev evaluation on the committed sports-equivalence label set."""

import os

import pytest
from jev_cases import contracts, load_cases

from market_agent.domain import Platform
from market_agent.domain.matching import match_candidates
from market_agent.jev import JevReviewer

pytestmark = [
    pytest.mark.live_smoke,
    pytest.mark.skipif(os.getenv("RUN_LIVE_JEV") != "1", reason="set RUN_LIVE_JEV=1"),
]


async def test_jev_semantic_policy_has_no_unsafe_automatic_decisions():
    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        pytest.skip("set AI_GATEWAY_API_KEY")
    reviewer = JevReviewer(api_key)
    rows = []
    try:
        for case in load_cases():
            left, right = contracts(case)
            truncated = (
                {(Platform.KALSHI, right.market_id)} if case.get("right_truncated") else set()
            )
            deterministic = match_candidates([left], [right], truncated=truncated)
            reviewed = await reviewer.review(deterministic, [left, right])
            pair = reviewed.pairs[0]
            rows.append(
                {
                    "name": case["name"],
                    "label": case["label"],
                    "deterministic": deterministic.pairs[0].verdict,
                    "final": pair.verdict,
                    "jev": pair.semantic_review.model_dump(mode="json")
                    if pair.semantic_review
                    else None,
                }
            )
    finally:
        await reviewer.aclose()

    semantic_actions = [
        row
        for row in rows
        if row["deterministic"] == "ambiguous"
        and row["final"] != "ambiguous"
        and row["jev"] is not None
    ]
    print(rows)
    assert len(semantic_actions) >= 2
    assert all(row["final"] == row["label"] for row in semantic_actions)
    assert all(
        row["label"] == "equivalent"
        for row in rows
        if row["final"] == "equivalent" and row["deterministic"] == "ambiguous"
    )
