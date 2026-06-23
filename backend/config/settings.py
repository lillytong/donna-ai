"""Central configuration. The ONLY place env vars are read (CLAUDE.md)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelTiers(BaseSettings):
    """Model assignments by consequence tier (DD-35). Never hardcoded in app code."""

    model_config = SettingsConfigDict(env_prefix="DONNA_MODEL_")

    high: str = "claude-opus-4-8"
    medium: str = "claude-sonnet-4-6"
    low: str = "claude-haiku-4-5-20251001"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str = ""
    redline_author: str = Field(default="", alias="DONNA_REDLINE_AUTHOR")
    operator_actor: str = "operator"
    log_level: str = "INFO"

    models: ModelTiers = Field(default_factory=ModelTiers)


@lru_cache
def get_settings() -> Settings:
    return Settings()
