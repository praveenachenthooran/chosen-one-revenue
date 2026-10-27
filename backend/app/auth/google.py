"""Google Workspace sign-in through Authlib (OpenID Connect, authorization-code flow + PKCE).

Authlib stores the OAuth state, nonce and PKCE verifier in the signed session, and rejects a
callback whose state does not match or whose ID token nonce/signature/audience is wrong.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import CSRF_HEADER, CurrentUser, optional_user, require_csrf
from app.auth.permissions import role_names
from app.auth.roles import EDIT_ANY_ROLES
from app.config import Settings, get_settings
from app.database import get_db_session
from app.models import User
from app.schemas import AuthStatus, AuthUser

logger = logging.getLogger(__name__)

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
router = APIRouter(prefix="/auth", tags=["auth"])

_clients: dict[tuple[str, str], StarletteOAuth2App] = {}


def google_client(settings: Settings) -> StarletteOAuth2App:
    """One Authlib client per credential set (so tests and rotated secrets get fresh clients)."""
    assert settings.google_client_id and settings.google_client_secret
    secret = settings.google_client_secret.get_secret_value()
    key = (settings.google_client_id, secret)
    if key not in _clients:
        registry = OAuth()
        client = registry.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=secret,
            server_metadata_url=GOOGLE_METADATA_URL,
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )
        assert isinstance(client, StarletteOAuth2App)
        _clients[key] = client
    return _clients[key]


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str


class SignInDenied(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def verified_identity(userinfo: dict[str, object] | None, settings: Settings) -> GoogleIdentity:
    """Accept only a verified Google identity; the email domain alone never authorizes anyone."""
    if not userinfo:
        raise SignInDenied("failed")
    sub, email = userinfo.get("sub"), userinfo.get("email")
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
        raise SignInDenied("failed")
    if userinfo.get("email_verified") is not True:
        raise SignInDenied("email_unverified")
    if settings.google_hosted_domain and userinfo.get("hd") != settings.google_hosted_domain:
        raise SignInDenied("wrong_domain")
    return GoogleIdentity(sub=sub, email=email.strip().lower())


async def resolve_provisioned_user(session: AsyncSession, identity: GoogleIdentity) -> User:
    """Find the explicitly provisioned user for a verified identity.

    A user already bound to a Google `sub` is matched on it alone. An unbound, provisioned user
    is matched on the verified email once, and bound to that `sub` from then on.
    """
    roles = selectinload(User.roles)
    user = await session.scalar(select(User).where(User.google_sub == identity.sub).options(roles))
    if user is None:
        user = await session.scalar(
            select(User)
            .where(func.lower(User.email) == identity.email, User.google_sub.is_(None))
            .options(roles)
        )
        if user is not None:
            user.google_sub = identity.sub
    if user is None or not user.is_provisioned:
        raise SignInDenied("not_provisioned")
    return user


def _frontend_redirect(settings: Settings, error: str | None = None) -> RedirectResponse:
    url = str(settings.frontend_url)
    if error:
        url += ("&" if "?" in url else "?") + urlencode({"auth_error": error})
    return RedirectResponse(url, status_code=303)


@router.get("/login", include_in_schema=False)
async def login(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> RedirectResponse:
    if not settings.google_configured:
        return _frontend_redirect(settings, "not_configured")
    extra = {"hd": settings.google_hosted_domain} if settings.google_hosted_domain else {}
    response = await google_client(settings).authorize_redirect(
        request, str(settings.google_redirect_uri), prompt="select_account", **extra
    )
    assert isinstance(response, RedirectResponse)
    return response


@router.get("/google/callback", include_in_schema=False)
async def callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    if not settings.google_configured:
        return _frontend_redirect(settings, "not_configured")
    try:
        token = await google_client(settings).authorize_access_token(request)
        identity = verified_identity(token.get("userinfo"), settings)
        user = await resolve_provisioned_user(session, identity)
    except SignInDenied as denied:
        logger.info("Sign-in denied: %s", denied.code)  # reason only; no email or token
        request.session.clear()
        return _frontend_redirect(settings, denied.code)
    except OAuthError, httpx.HTTPError:
        logger.info("Sign-in failed during the OAuth exchange")
        request.session.clear()
        return _frontend_redirect(settings, "failed")

    user.last_login_at = func.now()  # pyright: ignore[reportAttributeAccessIssue]
    await session.commit()
    request.session.clear()  # drop OAuth state; start a fresh session (no fixation)
    request.session.update(
        {"user_id": user.id, "csrf": secrets.token_urlsafe(32), "auth_at": int(time.time())}
    )
    return _frontend_redirect(settings)


def auth_user(user: User) -> AuthUser:
    roles = sorted(role_names(user))
    return AuthUser(
        id=user.id,
        name=user.name,
        email=user.email,
        roles=roles,
        can_edit_all=bool(set(roles) & EDIT_ANY_ROLES),
    )


@router.get("/me", response_model=AuthStatus)
async def me(request: Request, user: Annotated[User | None, Depends(optional_user)]) -> AuthStatus:
    """Authentication status. Always 200 so the app can tell signed-out from API-down."""
    if user is None:
        return AuthStatus(authenticated=False)
    csrf = request.session.get("csrf")
    return AuthStatus(
        authenticated=True,
        user=auth_user(user),
        csrf_token=csrf if isinstance(csrf, str) else None,
        csrf_header=CSRF_HEADER,
    )


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(request: Request, _: CurrentUser) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"authenticated": False})
