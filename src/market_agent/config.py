"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import HttpUrl, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


class Settings(BaseSettings):
    """Validated settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openai_api_key: SecretStr
    tavily_api_key: SecretStr
    openai_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    log_level: str = "INFO"
    port: int = 8080


def _format_validation_error(error: ValidationError) -> str:
    missing = [str(item["loc"][0]).upper() for item in error.errors() if item["type"] == "missing"]
    if missing:
        return f"Missing required environment variables: {', '.join(sorted(missing))}"
    fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
    return f"Invalid application configuration: {', '.join(fields)}"


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Load and cache settings, replacing Pydantic internals with a clear startup error."""

    try:
        # Values are supplied by BaseSettings at runtime, which static analysis cannot infer.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_format_validation_error(error)) from error
