import pytest

from market_agent.mcp import server_environment

pytestmark = pytest.mark.unit


def test_tavily_key_is_forwarded_to_the_tavily_server_only(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", " tvly-test ")

    assert server_environment("tavily") == {"TAVILY_API_KEY": "tvly-test"}
    assert server_environment("kalshi") is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_leaves_keyless_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TAVILY_API_KEY", value)

    assert server_environment("tavily") is None
