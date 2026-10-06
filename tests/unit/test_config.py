from pathlib import Path

import pytest
from pydantic import SecretStr

from market_agent.config import ConfigurationError, load_settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Never read the repository's ignored .env or inherit a developer's real keys.
    monkeypatch.chdir(tmp_path)
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


@pytest.mark.unit
def test_missing_required_configuration_has_clear_error() -> None:
    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert str(caught.value) == (
        "Missing required environment variables: GEMINI_API_KEY or OPENAI_API_KEY"
    )


@pytest.mark.unit
def test_valid_configuration_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-placeholder")
    monkeypatch.setenv("PORT", "9000")

    settings = load_settings()

    assert settings.port == 9000
    assert settings.model_api_key == SecretStr("test-openai-placeholder")
    assert settings.model_name == "gpt-5"


@pytest.mark.unit
def test_gemini_configuration_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-placeholder")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")

    settings = load_settings()

    assert settings.use_gemini
    assert settings.model_api_key == SecretStr("test-gemini-placeholder")
    assert settings.model_name == "gemini-3.8-flash"


@pytest.mark.unit
def test_local_model_host_header_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-placeholder")
    monkeypatch.setenv("OPENAI_HOST_HEADER", "127.0.0.1:8000")
    assert load_settings().openai_host_header == "127.0.0.1:8000"
    load_settings.cache_clear()
    monkeypatch.setenv("OPENAI_HOST_HEADER", "invalid host header")
    with pytest.raises(ConfigurationError):
        load_settings()
