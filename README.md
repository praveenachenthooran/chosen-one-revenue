# The Chosen One

The Chosen One is BIG's internal revenue forecasting application. This repository currently contains the Milestone 0 development foundation only: a React frontend, FastAPI backend, PostgreSQL database configuration, tests, and local containers. Revenue forecasting features are intentionally not implemented yet.

## Required tools

- Python 3.14
- [uv](https://docs.astral.sh/uv/)
- Node.js 22.13 or newer and npm
- Docker Desktop with Docker Compose

## Structure

```text
backend/       FastAPI application, Alembic, and Python tests
frontend/      React, TypeScript, Vite, and Vitest
reference/     Untracked internal requirements, designs, and demo data
scripts/       Project helper scripts
tests/         Reserved for future cross-application tests
```

## Local setup

1. Create the backend environment file:

   ```powershell
   Copy-Item backend/.env.example backend/.env
   ```

2. Replace development placeholders in `backend/.env` as needed. Never commit this file. The local Compose database values in the example are development-only.
3. Install dependencies:

   ```powershell
   Set-Location backend
   uv sync --dev
   Set-Location ../frontend
   npm install
   Set-Location ..
   ```

## Start the application

The simplest option starts PostgreSQL, FastAPI, and Vite together:

```powershell
docker compose up --build
```

Then open <http://localhost:5173>. FastAPI documentation is at <http://localhost:8000/docs>.

For host-based development, start only PostgreSQL and run the application processes separately:

```powershell
docker compose up -d db
Set-Location backend
uv run uvicorn app.main:app --reload
```

In another terminal:

```powershell
Set-Location frontend
npm run dev
```

Vite proxies `/api/*` to FastAPI and removes the `/api` prefix, so `/api/health` reaches FastAPI's `/health` endpoint.

## Environment variables

All backend settings are documented in `backend/.env.example`. `DATABASE_URL` must use SQLAlchemy's async PostgreSQL form (`postgresql+asyncpg://...`). OAuth, session, and Anthropic values are placeholders for later milestones and are not used by current application code.

## Quality checks

```powershell
Set-Location backend
uv run pytest
uv run ruff check .
uv run pyright
uv run alembic current

Set-Location ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

## Troubleshooting

- **Port already in use:** stop the process using ports 5173, 8000, or 5432, or change the relevant Compose port mapping.
- **Database connection refused:** wait for `docker compose ps` to show PostgreSQL as healthy, then retry.
- **Changed database credentials:** remove the local development volume with `docker compose down -v` only if discarding local database data is acceptable.
- **npm is blocked by PowerShell execution policy:** run `npm.cmd` instead of `npm`.
- **Stale dependencies:** run `uv sync --dev` in `backend` and `npm install` in `frontend`.

## Internal reference files

The entire `reference/` directory is ignored by Git. Add the official documents and demo data locally using the filenames listed in the project brief; do not commit them.

