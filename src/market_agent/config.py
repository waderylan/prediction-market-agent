"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr, ValidationError, model_validator
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
    tavily_api_key: SecretStr | None = None
    ai_gateway_api_key: SecretStr | None = None
    openai_model: str = "gpt-5"
    llm_timeout_seconds: float = Field(default=60, ge=1, le=180)
    openai_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    jev_enabled: bool = False
    jev_force_review: bool = False
    jev_timeout_seconds: float = Field(default=3, ge=0.5, le=10)
    jev_equivalent_threshold: float = Field(default=0.9, ge=0, le=1)
    jev_different_threshold: float = Field(default=0.75, ge=0, le=1)
    jev_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    log_level: str = "INFO"
    port: int = Field(default=8080, ge=1, le=65535)

    @model_validator(mode="after")
    def jev_key_required_when_enabled(self) -> "Settings":
        if self.jev_enabled and (
            self.ai_gateway_api_key is None
            or not self.ai_gateway_api_key.get_secret_value().strip()
        ):
            raise ValueError("AI_GATEWAY_API_KEY is required when JEV_ENABLED is true")
        return self


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
