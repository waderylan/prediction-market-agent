import pytest

from market_agent.config import ConfigurationError, load_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


@pytest.mark.unit
def test_missing_required_configuration_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert str(caught.value) == "Missing required environment variables: OPENAI_API_KEY"


@pytest.mark.unit
def test_valid_configuration_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-placeholder")
    monkeypatch.setenv("PORT", "9000")

    settings = load_settings()

    assert settings.port == 9000
    assert settings.openai_api_key.get_secret_value() == "test-openai-placeholder"


@pytest.mark.unit
def test_jev_requires_its_dedicated_gateway_key_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert "Invalid application configuration" in str(caught.value)


@pytest.mark.unit
def test_jev_configuration_is_typed_and_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-jev-placeholder")
    monkeypatch.setenv("JEV_EQUIVALENT_THRESHOLD", "0.95")

    settings = load_settings()

    assert settings.jev_enabled
    assert settings.ai_gateway_api_key is not None
    assert settings.ai_gateway_api_key.get_secret_value() == "test-jev-placeholder"
    assert settings.jev_equivalent_threshold == 0.95
