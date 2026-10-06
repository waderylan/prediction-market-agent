"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr, ValidationError
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

    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    tavily_api_key: SecretStr | None = None
    openai_model: str = "gpt-5"
    llm_timeout_seconds: float = Field(default=60, ge=1, le=180)
    openai_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    openai_host_header: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.:-]{1,100}$")
    log_level: str = "INFO"
    port: int = Field(default=8080, ge=1, le=65535)

    @property
    def use_gemini(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value())

    @property
    def model_api_key(self) -> SecretStr:
        key = self.gemini_api_key if self.use_gemini else self.openai_api_key
        if key is None:
            raise ConfigurationError(
                "Missing required environment variables: GEMINI_API_KEY or OPENAI_API_KEY"
            )
        return key

    @property
    def model_name(self) -> str:
        return self.gemini_model if self.use_gemini else self.openai_model


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
        settings = Settings()
        if not settings.model_api_key.get_secret_value():
            raise ConfigurationError(
                "Missing required environment variables: GEMINI_API_KEY or OPENAI_API_KEY"
            )
        return settings
    except ValidationError as error:
        raise ConfigurationError(_format_validation_error(error)) from error
