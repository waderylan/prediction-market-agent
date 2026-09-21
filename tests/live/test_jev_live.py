"""Opt-in Jev evaluation on the committed sports-equivalence label set."""

import os
import sys

import pytest
from jev_cases import contracts, load_cases, market_details
from langchain_mcp_adapters.client import MultiServerMCPClient

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


async def test_jev_mcp_live_protocol_review(monkeypatch: pytest.MonkeyPatch):
    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        pytest.skip("set AI_GATEWAY_API_KEY")
    monkeypatch.setenv("JEV_FORCE_REVIEW", "true")
    case = next(item for item in load_cases() if item["name"] == "explicit_cancellation_conflict")
    left, right = market_details(case)
    adapter = MultiServerMCPClient(
        {
            "jev": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "market_agent.mcp.jev"],
            }
        }
    )
    async with adapter.session("jev") as session:
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert tools == {"jev_review_contracts"}
        result = await session.call_tool(
            "jev_review_contracts",
            {
                "polymarket_contract": left.model_dump(mode="json"),
                "kalshi_contract": right.model_dump(mode="json"),
            },
        )

    assert not result.isError
    assert result.structuredContent["deterministic_assessment"]["verdict"] == "different"
    assert result.structuredContent["final_assessment"]["verdict"] == "different"
    assert result.structuredContent["jev_called"] is True
