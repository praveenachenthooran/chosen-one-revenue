from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    session_secret: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")


@lru_cache
def get_settings() -> Settings:
    return Settings()
