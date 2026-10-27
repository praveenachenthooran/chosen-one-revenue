"""Google sign-in, sessions, CSRF and provisioning. Google is always mocked."""

import time
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import google_client
from app.config import Settings, get_settings
from app.main import app
from app.models import User
from app.provisioning import ProvisioningError, grant, list_users, revoke
from tests.helpers import FakeGoogle, csrf_headers, make_settings, sign_in


def error_of(response: object) -> str | None:
    location = getattr(response, "headers")["location"]  # noqa: B009
    return parse_qs(urlparse(location).query).get("auth_error", [None])[0]


async def provision(session: AsyncSession, email: str = "pat@corp.test", **kwargs: object) -> User:
    return await grant(
        session,
        email=email,
        roles=kwargs.get("roles", ["Finance"]),  # type: ignore[arg-type]
        code=None,
        name="Pat Person",
    )


# ---- status and login redirect -----------------------------------------------------------


async def test_status_is_signed_out_by_default(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": False,
        "user": None,
        "csrf_token": None,
        "csrf_header": None,
    }


async def test_login_without_google_credentials_reports_not_configured(
    client: AsyncClient,
) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(
        google_client_id=None, google_client_secret=None, google_redirect_uri=None
    )
    response = await client.get("/auth/login")
    assert response.status_code == 303
    assert error_of(response) == "not_configured"


async def test_login_redirects_to_google_with_state_nonce_and_pkce(client: AsyncClient) -> None:
    settings = make_settings()
    google_client(settings).server_metadata = {  # skip the network metadata fetch
        "_loaded_at": time.time(),
        "issuer": "https://accounts.google.com",
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    }
    response = await client.get("/auth/login")

    assert response.status_code == 302
    target = urlparse(response.headers["location"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["client_id"] == [settings.google_client_id]
    assert query["redirect_uri"] == [
        str(settings.google_redirect_uri)
    ]  # from settings, not request
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
    assert len(query["state"][0]) >= 16 and len(query["nonce"][0]) >= 16
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"]
    cookie = response.headers["set-cookie"].lower()
    assert "chosen_one_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie  # development/test only; hosted environments set it


async def test_callback_with_unknown_state_is_rejected_by_the_real_state_check(
    client: AsyncClient,
) -> None:
    """No mocking here: Authlib itself refuses a callback it did not start."""
    response = await client.get("/auth/google/callback", params={"code": "abc", "state": "forged"})
    assert response.status_code == 303 and error_of(response) == "failed"
    assert (await client.get("/auth/me")).json()["authenticated"] is False


async def test_oauth_exchange_errors_deny_sign_in(
    client: AsyncClient, fake_google: FakeGoogle
) -> None:
    fake_google.fail_with_oauth_error()
    response = await client.get("/auth/google/callback", params={"code": "x", "state": "y"})
    assert error_of(response) == "failed"


# ---- who may sign in ---------------------------------------------------------------------


async def test_provisioned_user_signs_in_and_identity_is_bound(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    user = await provision(session, roles=["Client Partner"])
    response = await sign_in(client, fake_google, email="PAT@corp.test", sub="g-123")

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:5173/"
    status = (await client.get("/auth/me")).json()
    assert status["authenticated"] and status["user"]["roles"] == ["Client Partner"]
    assert status["user"]["id"] == user.id and status["csrf_token"]
    await session.refresh(user)
    assert user.google_sub == "g-123" and user.last_login_at is not None


async def test_bound_identity_is_matched_on_sub_even_if_the_email_changes(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test", sub="g-123")
    again = await sign_in(client, fake_google, email="renamed@corp.test", sub="g-123")
    assert error_of(again) is None
    assert (await client.get("/auth/me")).json()["authenticated"]


async def test_email_of_a_user_bound_to_another_google_account_is_refused(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test", sub="g-123")
    other = await sign_in(client, fake_google, email="pat@corp.test", sub="g-someone-else")
    assert error_of(other) == "not_provisioned"


@pytest.mark.parametrize("email", ["stranger@corp.test", "anyone@bitsinglass.com"])
async def test_unknown_google_accounts_are_denied_whatever_their_domain(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession, email: str
) -> None:
    await provision(session)  # someone is provisioned, but not this account
    response = await sign_in(client, fake_google, email=email, hd="bitsinglass.com")
    assert error_of(response) == "not_provisioned"
    assert (await client.get("/auth/me")).json()["authenticated"] is False
    assert (await client.get("/v1/projects")).status_code == 401


async def test_imported_workbook_people_cannot_sign_in(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    session.add(User(code="AC", name="Avery Chen", email="avery.chen@example.com"))
    await session.commit()
    response = await sign_in(client, fake_google, email="avery.chen@example.com")
    assert error_of(response) == "not_provisioned"


async def test_revoked_or_never_provisioned_user_is_denied(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    user = await provision(session)
    await revoke(session, email="pat@corp.test", code=None, reset_identity=False)
    assert error_of(await sign_in(client, fake_google, email="pat@corp.test")) == "not_provisioned"
    await session.refresh(user)
    assert not user.is_provisioned


async def test_unverified_email_is_refused(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    response = await sign_in(client, fake_google, email="pat@corp.test", email_verified=False)
    assert error_of(response) == "email_unverified"


async def test_hosted_domain_is_an_extra_restriction_never_a_grant(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(google_hosted_domain="corp.test")
    await provision(session)
    wrong = await sign_in(client, fake_google, email="pat@corp.test", hd="gmail.example")
    assert error_of(wrong) == "wrong_domain"
    right = await sign_in(client, fake_google, email="pat@corp.test", hd="corp.test")
    assert error_of(right) is None
    # right domain but not provisioned: still denied
    stranger = await sign_in(client, fake_google, email="x@corp.test", hd="corp.test")
    assert error_of(stranger) == "not_provisioned"


@pytest.mark.parametrize(("email", "sub"), [("pat@corp.test", ""), ("", "g-1")])
async def test_incomplete_identity_is_refused(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession, email: str, sub: str
) -> None:
    await provision(session)
    response = await sign_in(client, fake_google, email=email, sub=sub)
    assert error_of(response) == "failed"


# ---- sessions, CSRF, logout --------------------------------------------------------------


async def test_every_forecast_endpoint_rejects_anonymous_requests(client: AsyncClient) -> None:
    for path in (
        "/v1/projects",
        "/v1/projects/1",
        "/v1/projects/1/revenue",
        "/v1/projects/1/history",
        "/v1/months",
        "/v1/revenue/monthly-totals",
        "/v1/imports",
        "/v1/imports/latest",
    ):
        response = await client.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"]["code"] == "not_authenticated"
    put = await client.put(
        "/v1/projects/1/revenue/2026-10-01", json={"amount": 1, "expected_amount": None}
    )
    assert put.status_code == 401


async def test_logout_needs_the_csrf_token_and_ends_the_session(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test")
    headers = await csrf_headers(client)

    assert (await client.post("/auth/logout")).status_code == 403
    assert (await client.post("/auth/logout", headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.get("/auth/me")).json()["authenticated"] is True

    assert (await client.post("/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/auth/me")).json()["authenticated"] is False
    assert (await client.get("/v1/projects")).status_code == 401


async def test_a_new_session_is_started_at_sign_in(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test")
    first = await csrf_headers(client)
    await sign_in(client, fake_google, email="pat@corp.test")
    assert await csrf_headers(client) != first  # CSRF token rotates with each sign-in


async def test_sessions_expire(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test")
    assert (await client.get("/v1/months")).status_code in (200, 404)

    app.dependency_overrides[get_settings] = lambda: make_settings(session_max_age_seconds=-1)
    assert (await client.get("/v1/months")).status_code == 401
    assert (await client.get("/auth/me")).json()["authenticated"] is False


async def test_removing_provisioning_takes_effect_immediately(
    client: AsyncClient, fake_google: FakeGoogle, session: AsyncSession
) -> None:
    user = await provision(session)
    await sign_in(client, fake_google, email="pat@corp.test")
    assert (await client.get("/auth/me")).json()["authenticated"]

    await session.execute(update(User).where(User.id == user.id).values(is_provisioned=False))
    await session.commit()
    assert (await client.get("/v1/projects")).status_code == 401
    assert (await client.get("/auth/me")).json()["authenticated"] is False


# ---- configuration -----------------------------------------------------------------------


def test_hosted_environments_require_secure_cookies_and_a_strong_secret() -> None:
    for env in ("staging", "production"):
        hosted = Settings(_env_file=None, app_env=env)  # pyright: ignore[reportCallIssue]
        assert hosted.cookie_secure and hosted.is_hosted
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            hosted.require_session_secret()
        weak = Settings(_env_file=None, app_env=env, session_secret="short")  # pyright: ignore[reportCallIssue]
        with pytest.raises(RuntimeError):
            weak.require_session_secret()
        strong = Settings(_env_file=None, app_env=env, session_secret="s" * 40)  # pyright: ignore[reportCallIssue]
        assert strong.require_session_secret() == "s" * 40
    local = Settings(_env_file=None, app_env="development")  # pyright: ignore[reportCallIssue]
    assert not local.cookie_secure and local.require_session_secret() == ""


def test_google_is_configured_only_when_every_setting_is_present() -> None:
    assert make_settings().google_configured
    assert not make_settings(google_client_secret=None).google_configured
    assert not make_settings(google_redirect_uri=None).google_configured


def test_secrets_are_not_exposed_by_settings_repr() -> None:
    text = repr(make_settings())
    assert "test-client-secret" not in text and "t" * 48 not in text


# ---- provisioning command ----------------------------------------------------------------


async def test_provisioning_adopts_an_imported_person_without_inventing_emails(
    session: AsyncSession,
) -> None:
    session.add(User(code="AC", name="Avery Chen", email="avery.chen@example.com"))
    await session.commit()

    user = await grant(
        session, email="Avery@Corp.Test", roles=["Client Partner"], code="AC", name=None
    )
    assert user.email == "avery@corp.test" and user.is_provisioned and user.code == "AC"
    assert [u.name for u in await list_users(session)] == ["Avery Chen"]


async def test_provisioning_validates_input(session: AsyncSession) -> None:
    session.add_all(
        [User(code="AC", name="Avery", email="avery@corp.test"), User(code="BB", name="Bo")]
    )
    await session.commit()
    with pytest.raises(ProvisioningError, match="valid email"):
        await grant(session, email="nope", roles=["Finance"], code=None, name="X")
    with pytest.raises(ProvisioningError, match="Unknown role"):
        await grant(session, email="a@corp.test", roles=["Wizard"], code=None, name="X")
    with pytest.raises(ProvisioningError, match="at least one"):
        await grant(session, email="a@corp.test", roles=[], code=None, name="X")
    with pytest.raises(ProvisioningError, match="--name"):
        await grant(session, email="new@corp.test", roles=["Finance"], code=None, name=None)
    with pytest.raises(ProvisioningError, match="already belongs"):
        await grant(session, email="avery@corp.test", roles=["Finance"], code="BB", name="X")
    with pytest.raises(ProvisioningError, match="No imported person"):
        await grant(session, email="z@corp.test", roles=["Finance"], code="ZZ", name="X")
    assert not (await session.scalars(select(User).where(User.is_provisioned))).all()


async def test_workbook_role_names_are_accepted_as_aliases(session: AsyncSession) -> None:
    user = await grant(session, email="s@corp.test", roles=["Seller / AE", "Admin"], code=None,
                       name="Sam")  # fmt: skip
    await session.refresh(user, ["roles"])
    assert [r.name for r in user.roles] == ["Administrator", "Seller / Account Executive"]
