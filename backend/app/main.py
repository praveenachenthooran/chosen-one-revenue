import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.auth.google import router as auth_router
from app.config import get_settings
from app.database import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="The Chosen One API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.frontend_url).rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Signed, HttpOnly session cookie. SameSite=Lax is required for the Google redirect back to us;
# state-changing requests are additionally protected by a CSRF token (see app.auth.deps).
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.require_session_secret() or secrets.token_urlsafe(48),
    session_cookie="chosen_one_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.cookie_secure,
)

app.include_router(auth_router)
app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "chosen-one-api"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ready", "database": "connected"}


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def frontend() -> FileResponse:
        return FileResponse(static_dir / "index.html")
