"""Test fixtures. Tests run against a real PostgreSQL database named `chosen_one_test`.

The database is created on demand on the same server as the development database (start it
with `docker compose up -d db`), rebuilt from the Alembic migrations once per test session,
and truncated between tests. The development database is never touched.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import asyncpg  # pyright: ignore[reportMissingTypeStubs]
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from authlib.integrations.starlette_client import StarletteOAuth2App
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DEV_URL = "postgresql+asyncpg://chosen_one_dev:chosen_one_dev_password@localhost:5432/chosen_one"
TEST_DB_URL = make_url(os.environ.get("TEST_DATABASE_URL", DEV_URL)).set(database="chosen_one_test")
# Must be set before `app` is imported anywhere: app.database builds its engine at import time.
os.environ["DATABASE_URL"] = TEST_DB_URL.render_as_string(hide_password=False)
os.environ["APP_ENV"] = "test"

from app.config import get_settings  # noqa: E402
from app.database import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from tests.helpers import FakeGoogle, csrf_headers, make_settings, sign_in  # noqa: E402
from tests.workbook_builder import WorkbookBuilder  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


async def _recreate_database() -> None:
    admin = await asyncpg.connect(
        user=TEST_DB_URL.username,
        password=TEST_DB_URL.password,
        host=TEST_DB_URL.host,
        port=TEST_DB_URL.port,
        database="postgres",
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_URL.database
        )
        if not exists:
            await admin.execute(f'CREATE DATABASE "{TEST_DB_URL.database}"')
    finally:
        await admin.close()
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    asyncio.run(_recreate_database())
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    test_engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with test_engine.begin() as connection:
        tables = await connection.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await connection.execute(text(f"TRUNCATE {tables.scalar_one()} RESTART IDENTITY CASCADE"))
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as db_session:
        yield db_session


@pytest.fixture
def override_db(engine: AsyncEngine) -> Callable[[], None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as db_session:
            yield db_session

    def apply() -> None:
        app.dependency_overrides[get_db_session] = _session

    return apply


@pytest.fixture
def builder(tmp_path: Path) -> WorkbookBuilder:
    return WorkbookBuilder(tmp_path / "chosen_one_test.xlsx")


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    """Replace Google's token exchange; the real state/redirect handling is left in place."""
    google = FakeGoogle()

    async def authorize_access_token(self: object, request: object, **kwargs: object) -> object:
        return await google.authorize_access_token(self, request, **kwargs)

    monkeypatch.setattr(StarletteOAuth2App, "authorize_access_token", authorize_access_token)
    return google


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine, override_db: Callable[[], None]
) -> AsyncIterator[AsyncClient]:
    """An anonymous client with the test database and Google-configured settings."""
    override_db()
    app.dependency_overrides[get_settings] = lambda: make_settings()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


SignedIn = Callable[[str], Awaitable[tuple[AsyncClient, dict[str, str]]]]


@pytest_asyncio.fixture
async def as_user(client: AsyncClient, fake_google: FakeGoogle) -> AsyncIterator[SignedIn]:
    """Sign a *new* client in as the given (already provisioned) email.

    Returns (client, csrf_headers). Each call gets its own cookie jar, so several people can act
    in one test.
    """
    clients: list[AsyncClient] = []

    async def factory(email: str) -> tuple[AsyncClient, dict[str, str]]:
        http = AsyncClient(transport=client._transport, base_url="http://test")  # pyright: ignore[reportPrivateUsage]
        clients.append(http)
        response = await sign_in(http, fake_google, email=email)
        assert response.status_code == 303, response.text
        assert "auth_error" not in response.headers["location"], response.headers["location"]
        return http, await csrf_headers(http)

    yield factory
    for http in clients:
        await http.aclose()
