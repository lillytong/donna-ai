"""Central configuration. The ONLY place env vars are read (CLAUDE.md)."""

from functools import lru_cache

from pydantic import Field
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
    # Retry-with-exponential-backoff for transient LLM failures (timeouts, rate limits,
    # 5xx/connection) in services/llm.py. Per-attempt timeout stays `timeout_s`; total
    # attempts = 1 + llm_max_retries. Knobs not hardcoded (DD-35).
    llm_max_retries: int = 2
    llm_backoff_base_s: float = 0.5
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
    donna_recommendation_max_tokens: int = 4096
    donna_recommendation_temperature: float = 1.0
    # F08d Donna-assisted clause drafting: a complete clause (heading + body) grounded in
    # deal type + surrounding clauses, at the capable tier (high/Opus — drafted language is
    # high-consequence, DD-35). Opus 4.8 rejects temperature 0.0, so this tier is pinned 1.0.
    clause_draft_max_tokens: int = 4096
    clause_draft_temperature: float = 1.0
    # F10b context-aware Donna chat (advise + draft): a grounded conversational turn at the
    # capable tier (high/Opus — advice/drafting is high-consequence, DD-35; mirrors F11/F08d).
    # Opus 4.8 rejects temperature 0.0, so this tier is pinned to 1.0.
    chat_advise_max_tokens: int = 4096
    chat_advise_temperature: float = 1.0
    # F10b brainstorm overlay (DD-73/DD-77). The exploratory turn is advice-grade, so it runs
    # at the high tier like chat_advise (Opus rejects temp 0.0 → 1.0). The on-close distillation
    # is internal/never counterparty-facing, so it runs at the medium tier (Sonnet, supports
    # 0.0 → deterministic). Limits/temps from config, not code (DD-35).
    brainstorm_chat_max_tokens: int = 4096
    brainstorm_chat_temperature: float = 1.0
    brainstorm_distill_max_tokens: int = 1024
    brainstorm_distill_temperature: float = 0.0
    # F03c per-change revision recommendation (DD-78): a per-hunk verdict + significance +
    # one-line reasoning + exact counter-language, at the capable tier (high/Opus — drafted
    # counter-language is high-consequence, DD-35; mirrors F11 donna_recommendation). Opus 4.8
    # rejects temperature 0.0, so this tier is pinned to 1.0. max_tokens >= 4096: generation
    # surfaces truncate at the 1024 default → JSON parse failure.
    revision_recommend_max_tokens: int = 4096
    revision_recommend_temperature: float = 1.0
    # F37 deal brief (DD-95): Donna distils a per-deal global-context brief from ONE whole-
    # contract read at import, at the capable tier (high/Opus — a wrong brief grounds every
    # later recommendation, so it is high-consequence; Opus 4.8 rejects temperature 0.0, so
    # this tier is pinned to 1.0). A whole-contract read needs a large output budget
    # (~6-8k tokens) AND a longer per-call timeout than the 30s default — the validating spike
    # took ~51s, so the wrapper's default would time out. Knobs from config, not code (DD-35).
    deal_brief_max_tokens: int = 8000
    deal_brief_temperature: float = 1.0
    deal_brief_timeout_s: float = 90.0
    # F03c auto-run-at-import cost guard (~1 Opus call per hunk): the import route fires
    # Donna's per-change recommendation in the background, but only for reasonably-sized
    # diffs. Above this staged-change ceiling the auto-run is skipped (logged — no silent
    # cap; the operator can still trigger POST .../recommend manually). Not hardcoded (DD-35).
    revision_recommend_auto_max_changes: int = 50


class DistillationSettings(BaseSettings):
    """F30 negotiation-pattern distillation knobs (DD-76). Runs at the MEDIUM tier
    (Sonnet — judgment, but internal and never counterparty-facing, DD-35). Sonnet
    supports temperature 0.0, so the extraction is pinned deterministic. Limits/temps
    and the consolidation thresholds are config, never hardcoded (DD-35)."""

    model_config = SettingsConfigDict(env_prefix="DONNA_DISTILL_")

    max_tokens: int = 1024
    temperature: float = 0.0
    # New-record + reinforcement confidence model (0..1). A new pattern starts at
    # `new_confidence`; a merge bumps it by `reinforce_increment`, capped at 1.0.
    new_confidence: float = 0.5
    reinforce_increment: float = 0.15
    # Consolidation/prune (DD-76). Deal-close has a handler (settings_repo.update_deal)
    # but wiring it is outside this build's file scope, so consolidation falls back to the
    # N-counter: it runs once >= `consolidate_after_n` new patterns have been added since
    # the last consolidation. Prune a pattern unreinforced across >= `prune_deals` distinct
    # deals that is still at minimum evidence (never reinforced).
    consolidate_after_n: int = 5
    prune_deals: int = 3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str = ""
    # F25: the operator's organization identity (DD-44). A config value, not a DB
    # entity — surfaced read-only in Settings → Your Organization.
    operator_org_name: str = Field(default="", alias="DONNA_OPERATOR_ORG_NAME")
    # Explicit per-deployment author override, kept RAW (empty unless set) so it doubles as
    # the "explicit author?" signal the DB-aware resolver needs (services/operator_org_repo).
    redline_author: str = Field(default="", alias="DONNA_REDLINE_AUTHOR")
    operator_actor: str = "operator"
    log_level: str = "INFO"

    models: ModelTiers = Field(default_factory=ModelTiers)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    distillation: DistillationSettings = Field(default_factory=DistillationSettings)

    @property
    def export_author(self) -> str:
        """The config-resolved redline/export author (DD-44). Never blank, never 'Donna'.
        Priority: explicit DONNA_REDLINE_AUTHOR → org name → neutral default. The editable
        DB org-name override is layered on top in services/operator_org_repo."""
        return (
            self.redline_author.strip()
            or self.operator_org_name.strip()
            or DEFAULT_OPERATOR_ORG_NAME
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
