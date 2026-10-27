"""The Chosen One backend application."""

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr
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
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")

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
