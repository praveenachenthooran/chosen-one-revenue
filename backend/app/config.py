"""The Chosen One backend application."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SESSION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = (
        "postgresql+asyncpg://chosen_one_dev:chosen_one_dev_password@localhost:5432/chosen_one"
    )
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: AnyHttpUrl | None = None
    # Optional extra restriction (the OIDC "hd" claim). It never grants access on its own:
    # a user must also be provisioned in the application.
    google_hosted_domain: str | None = None
    session_secret: SecretStr | None = None
    session_max_age_seconds: int = 8 * 60 * 60
    anthropic_api_key: SecretStr | None = None
    # First month of the reporting fiscal year (1-12). April matches the supplied workbook and
    # PRD section 5; the latest business meeting said September. Left at April until the business
    # confirms; labels are computed on the fly, so changing it rewrites no stored data.
    fiscal_year_start_month: int = Field(default=4, ge=1, le=12)

    # --- HubSpot pipeline (Milestone 3) ---
    # "workbook" reads the supplied export; "api" is reserved for the future read-only live adapter.
    hubspot_source: Literal["workbook", "api"] = "workbook"
    hubspot_workbook_path: str | None = (
        None  # default: reference/03-demo-data/HubSpot_OnDeck_*.xlsx
    )
    hubspot_access_token: SecretStr | None = None  # read-only private-app token (live API only)
    # PRD: alert the owner and CRO when a Closed Won draft is unconfirmed after 2 business days.
    draft_confirmation_business_days: int = 2
    # PRD: sync at least every 60 minutes; the UI warns once the last good sync is older.
    hubspot_stale_after_minutes: int = 60
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")

    # --- QA engine and AI Revenue Detective (Milestone 5) ---
    # Thresholds from the PRD; each is configurable but defaults to the PRD value.
    qa_stale_days: int = 30  # rows in the 3-month window not updated for this long
    qa_mismatch_percent: Decimal = Decimal("10")  # HubSpot amount vs committed schedule
    qa_anomaly_min_amount: Decimal = Decimal("5000")  # month-over-month change, at least this ...
    qa_anomaly_min_percent: Decimal = Decimal(
        "30"
    )  # ... and at least this share of the prior month
    # Claude is used only when a key is present AND the enterprise data-use approval is switched on.
    anthropic_model: str = "claude-sonnet-5-5"
    ai_data_use_approved: bool = False
    ai_monthly_call_limit: int = (
        200  # investigations run by Claude per calendar month (cost ceiling)
    )
    ai_timeout_seconds: int = 60

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """Railway supplies postgresql://; SQLAlchemy needs the asyncpg driver named."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @property
    def ai_enabled(self) -> bool:
        """Claude may be called only with credentials and the approved data-use switch."""
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value()) and (
            self.ai_data_use_approved
        )

    @property
    def is_hosted(self) -> bool:
        """True for staging/production, where secure cookies and a real secret are mandatory."""
        return self.app_env in ("staging", "production")

    @property
    def cookie_secure(self) -> bool:
        return self.is_hosted

    @property
    def google_configured(self) -> bool:
        return bool(
            self.google_client_id and self.google_client_secret and self.google_redirect_uri
        )

    def require_session_secret(self) -> str:
        """Return the signing secret; refuse to run hosted without a strong one."""
        secret = self.session_secret.get_secret_value() if self.session_secret else ""
        if len(secret) >= MIN_SESSION_SECRET_LENGTH:
            return secret
        if self.is_hosted:
            raise RuntimeError(
                f"SESSION_SECRET must be set to at least {MIN_SESSION_SECRET_LENGTH} characters "
                f"when APP_ENV={self.app_env}"
            )
        return ""  # development/test: the caller generates an ephemeral per-process secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
