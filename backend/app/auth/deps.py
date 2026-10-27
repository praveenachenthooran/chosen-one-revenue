"""Request dependencies: the signed-in user and CSRF protection."""

import hmac
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import get_db_session
from app.models import User

CSRF_HEADER = "X-CSRF-Token"


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401, detail={"code": "not_authenticated", "message": "Sign in to continue."}
    )


async def optional_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User | None:
    """The signed-in user, or None. The database is consulted on every request, so removing
    someone's provisioning takes effect immediately rather than when their cookie expires."""
    user_id = request.session.get("user_id")
    auth_at = request.session.get("auth_at")
    if not isinstance(user_id, int) or not isinstance(auth_at, int | float):
        return None
    if time.time() - auth_at > settings.session_max_age_seconds:
        request.session.clear()
        return None
    user = await session.scalar(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    if user is None or not user.is_provisioned:
        request.session.clear()
        return None
    return user


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise _unauthenticated()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_csrf(request: Request, _: CurrentUser) -> None:
    """State-changing requests must echo the per-session token in a header (cross-site pages
    cannot read it), on top of the SameSite=Lax session cookie."""
    expected = request.session.get("csrf")
    supplied = request.headers.get(CSRF_HEADER, "")
    if not isinstance(expected, str) or not hmac.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=403,
            detail={"code": "csrf_failed", "message": "Missing or invalid CSRF token. Reload."},
        )
