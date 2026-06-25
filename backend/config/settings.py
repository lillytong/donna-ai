"""Central configuration. The ONLY place env vars are read (CLAUDE.md)."""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# DD-44/F25: neutral fallback when no operator org is configured. Never blank,
# never "Donna" — a brand-new deployment still authors redlines as an org.
DEFAULT_OPERATOR_ORG_NAME = "Operator Organization"


class ModelTiers(BaseSettings):
    """Model assignments by consequence tier (DD-35). Never hardcoded in app code."""

    model_config = SettingsConfigDict(env_prefix="DONNA_MODEL_")

    high: str = "claude-opus-4-8"
    medium: str = "claude-sonnet-4-6"
    low: str = "claude-haiku-4-5-20251001"


class LlmSettings(BaseSettings):
    """LLM call knobs that aren't the model id (DD-35: never hardcode limits/temps).

    `timeout_s` bounds every wrapper call; the `clause_search_*` fields size that
    surface's tiny structured answer (`{"node_id": ...}`)."""

    model_config = SettingsConfigDict(env_prefix="DONNA_LLM_")

    timeout_s: float = 30.0
    clause_search_max_tokens: int = 64
    clause_search_temperature: float = 0.0
    # F10 Donna Q&A: the capable-tier answer (a few sentences + citations) and the
    # cheap-tier rolling-summary update (DD-40). Limits/temps from config, not code (DD-35).
    donna_qa_max_tokens: int = 1024
    donna_qa_temperature: float = 0.0
    donna_summary_max_tokens: int = 256
    donna_summary_temperature: float = 0.0
    # F11 issue recommendation: a grounded rationale + draft position/counter-language at
    # the capable tier (high/Opus, DD-35 — counter-language is high-consequence). Opus 4.8
    # only supports temperature=1 (it rejects 0.0), so this tier's temp is pinned to 1.0.
    donna_recommendation_max_tokens: int = 1024
    donna_recommendation_temperature: float = 1.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str = ""
    # F25: the operator's organization identity (DD-44). A config value, not a DB
    # entity — surfaced read-only in Settings → Your Organization.
    operator_org_name: str = Field(default="", alias="DONNA_OPERATOR_ORG_NAME")
    # Explicit author override; when unset the org name flows in via the validator.
    redline_author: str = Field(default="", alias="DONNA_REDLINE_AUTHOR")
    operator_actor: str = "operator"
    log_level: str = "INFO"

    models: ModelTiers = Field(default_factory=ModelTiers)
    llm: LlmSettings = Field(default_factory=LlmSettings)

    @model_validator(mode="after")
    def _wire_export_author(self) -> "Settings":
        # DD-44/F25: the redline/export author is the operator org name — never
        # "Donna", never blank. Priority: explicit DONNA_REDLINE_AUTHOR → org name →
        # neutral default. Populating redline_author here means the export read-site
        # (services/export/redline.py: `redline_author or operator_actor`) resolves to
        # the org name with no change at that call site.
        if not self.redline_author.strip():
            self.redline_author = self.operator_org_name.strip() or DEFAULT_OPERATOR_ORG_NAME
        return self

    @property
    def export_author(self) -> str:
        """The resolved redline/export author (DD-44). Never blank, never 'Donna'."""
        return self.redline_author


@lru_cache
def get_settings() -> Settings:
    return Settings()
