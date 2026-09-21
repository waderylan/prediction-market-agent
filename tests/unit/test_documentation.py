"""Documentation and manifest assertions for the implemented third-server architecture."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_readme_documents_verified_game_state_surface_and_three_diagrams():
    readme = read("README.md")
    assert "Three independent MCP servers" in readme
    assert "sports_state_find_games" in readme
    assert "sports_state_get_game_state" in readme
    assert "ESPN public JSON" in readme
    assert "MLB StatsAPI" in readme
    assert "undocumented" in readme and "no SLA" in readme
    assert "sporting result does not establish prediction-market settlement" in readme
    assert readme.count("```mermaid") == 3
    assert "Sports-state MCP" in readme
    assert "Game-state MCP design" in readme
    assert "jev_review_contracts" not in readme
    assert "market_agent.mcp.jev" not in readme
    assert "both Python MCP servers" not in readme


def test_project_documents_match_current_capability_and_future_boundaries():
    proposal = read("docs/planning/PROJECT_PROPOSAL.md")
    plan = read("docs/planning/IMPLEMENTATION_PLAN.md")
    runtime = read("docs/research/MCP_VERTICAL_SLICE.md")
    testing = read("docs/research/TESTING.md")
    matching = read("docs/research/CONTRACT_MATCHING.md")
    evaluation = read("docs/research/MATCHING_VALUE_EVALUATION.md")
    feasibility = read("docs/research/MARKET_API_FEASIBILITY.md")
    design = read("docs/research/GAME_STATE_MCP.md")
    assert "third integrated MCP server" in proposal
    assert "| 6. Sports game-state MCP | Complete locally |" in plan
    assert (
        "| 7. Sports-aware event identity and deterministic contract matching | Complete; "
        "simplified by Milestone 8A |" in plan
    )
    assert "| 8. Jev sports-contract equivalence | Removed by Milestone 8A |" in plan
    assert "three separate Python stdio processes" in runtime
    assert "Milestone 8A" in runtime
    assert "live/test_game_state_mcp_live.py" in testing
    assert "final score cannot" in matching
    assert "Contract-to-contract equivalence" in matching
    assert "15/15" in evaluation and "29,254 input tokens" in evaluation
    assert "remove Jev completely" in evaluation
    assert "matching_report.market_to_game" in design
    assert "Current sporting state is a separate provider boundary" in feasibility
    for required in (
        "sports_state_find_games",
        "sports_state_get_game_state",
        "5 MiB",
        "Maximum 2",
        "30 seconds",
        "256",
        "invalid_game_ref",
        "MLB-only",
    ):
        assert required in design


def test_manifest_has_three_unique_python_servers_and_no_new_secret():
    manifest = json.loads(read("src/market_agent/mcp/servers.json"))
    assert set(manifest) == {"kalshi", "polymarket", "sports_state"}
    modules = [tuple(config["args"]) for config in manifest.values()]
    assert len(set(modules)) == 3
    assert all(config["transport"] == "stdio" for config in manifest.values())
    environment = read(".env.example")
    assert "ESPN" not in environment and "MLB" not in environment
