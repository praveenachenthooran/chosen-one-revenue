"""Test helpers for signing in with a mocked Google identity. No live Google calls are made."""

from dataclasses import dataclass, field
from typing import Any

from authlib.integrations.base_client.errors import OAuthError
from httpx import AsyncClient, Response

from app.config import Settings

REDIRECT_URI = "http://localhost:8000/auth/google/callback"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "google_client_id": "test-client-id.apps.googleusercontent.com",
        "google_client_secret": "test-client-secret",
        "google_redirect_uri": REDIRECT_URI,
        "session_secret": "t" * 48,
        "frontend_url": "http://localhost:5173",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


@dataclass
class FakeGoogle:
    """Stands in for Google's token exchange: whatever is set here is what 'Google' returns."""

    userinfo: dict[str, Any] | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=lambda: [])

    async def authorize_access_token(self, _self: object, request: object, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"access_token": "not-a-real-token", "userinfo": self.userinfo}

    def fail_with_oauth_error(self) -> None:
        self.error = OAuthError(error="invalid_grant")


async def sign_in(
    client: AsyncClient, google: FakeGoogle, *, email: str, sub: str | None = None, **claims: Any
) -> Response:
    """Run the callback exactly as Google would redirect to it, with a mocked identity."""
    google.error = None
    google.userinfo = {
        "sub": f"google-sub-{email}" if sub is None else sub,
        "email": email,
        "email_verified": True,
        "name": "Test Person",
        **claims,
    }
    return await client.get("/auth/google/callback", params={"code": "abc", "state": "xyz"})


async def csrf_headers(client: AsyncClient) -> dict[str, str]:
    status = (await client.get("/auth/me")).json()
    return {status["csrf_header"]: status["csrf_token"]}
