"""Documentation and manifest assertions for the implemented four-server architecture."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_readme_documents_product_setup_limits_and_architecture():
    readme = read("README.md")
    assert "Four independent MCP servers" in readme
    assert "sports_state_find_games" in readme
    assert "sports_state_get_game_state" in readme
    assert "sports_state_get_box_score" in readme
    assert "sports_state_list_players" in readme
    assert "sports_state_get_player_stats" in readme
    assert "sports_state_get_play_by_play" in readme
    assert "ESPN public JSON" in readme
    assert "MLB StatsAPI" in readme
    assert "undocumented" in readme and "no SLA" in readme
    assert "sporting result does not establish prediction-market settlement" in readme
    assert readme.count("```mermaid") == 3
    assert "Sports-state MCP" in readme
    assert "Sports-state MCP design" in readme
    assert "tavily_search_game_evidence" in readme
    assert "Tavily research MCP design" in readme
    assert "sports-information skill" in readme
    assert "FastAPI and LangGraph path remains the product" in readme
    assert "uv run python main.py" in readme
    assert "Cloud Run deployment uses one worker" in readme
    assert "ADVERSARIAL_TESTING_REPORT.md" in readme


def test_project_documents_match_current_capability_and_future_boundaries():
    proposal = read("docs/planning/PROJECT_PROPOSAL.md")
    plan = read("docs/planning/IMPLEMENTATION_PLAN.md")
    runtime = read("docs/research/MCP_VERTICAL_SLICE.md")
    testing = read("docs/research/TESTING.md")
    matching = read("docs/research/CONTRACT_MATCHING.md")
    feasibility = read("docs/research/MARKET_API_FEASIBILITY.md")
    design = read("docs/research/GAME_STATE_MCP.md")
    research = read("docs/research/WEB_RESEARCH_MCP.md")
    adversarial = read("docs/research/ADVERSARIAL_TESTING_REPORT.md")
    assert "fourth integrated MCP server" in proposal
    assert (
        "| 12. Optional sports-research snapshot ledger | Deferred until after deployment |" in plan
    )
    assert "| 13. Failure handling and verification | Complete locally |" in plan
    assert "unified all-in-one information workflow" in proposal
    assert "deferred until after deployment" in proposal
    assert ".agents/skills/sports-information/SKILL.md" in plan
    assert "eight market/state attempts" in runtime
    assert "four separate Python stdio processes" in runtime
    assert "live/test_game_state_mcp_live.py" in testing
    assert "ADVERSARIAL_TESTING_REPORT.md" in testing
    assert "final score cannot" in matching
    assert "Contract-to-contract equivalence" in matching
    assert "matching_report.market_to_game" in design
    assert "Current sporting state is a separate provider boundary" in feasibility
    assert "at most two Tavily searches" in research
    assert "same_matchup_date" in research
    assert "All four configured MCP servers" in adversarial
    assert "No test bloat" in adversarial
    for required in (
        "sports_state_find_games",
        "sports_state_get_game_state",
        "sports_state_get_box_score",
        "sports_state_list_players",
        "sports_state_get_player_stats",
        "sports_state_get_play_by_play",
        "5 MiB",
        "Maximum 2",
        "30 seconds",
        "256",
        "invalid_game_ref",
        "MLB-only",
    ):
        assert required in design


def test_manifest_has_four_unique_python_servers_and_optional_tavily_key():
    manifest = json.loads(read("src/market_agent/mcp/servers.json"))
    assert set(manifest) == {"kalshi", "polymarket", "sports_state", "tavily"}
    modules = [tuple(config["args"]) for config in manifest.values()]
    assert len(set(modules)) == 4
    assert all(config["transport"] == "stdio" for config in manifest.values())
    environment = read(".env.example")
    assert "ESPN" not in environment and "MLB" not in environment
    assert "TAVILY_API_KEY=" in environment
