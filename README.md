# The Chosen One

The Chosen One is BIG's internal revenue forecasting application. This repository contains the Milestone 0 foundation (React frontend, FastAPI backend, PostgreSQL, tests, local containers) and Milestone 1: the revenue data model, the Excel migration, a read-only forecast API and a read-only AG Grid forecast. Milestone 2 adds Google Workspace sign-in, role and row-level permissions, monthly editing and audit history. Milestone 3 adds the read-only HubSpot pipeline: weighted deals, Closed Won drafts and combined totals. AI features are not implemented yet.

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
docs/          Milestone notes and assumptions
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

## Milestone 1: import and view the forecast

1. Start PostgreSQL and apply migrations:

   ```powershell
   docker compose up -d db
   Set-Location backend
   uv run alembic upgrade head
   ```

2. Import the workbook (read-only access to the file; safe to repeat):

   ```powershell
   uv run python -m app.migration            # defaults to reference/03-demo-data/Chosen_One_Dummy_Data_FY27.xlsx
   uv run python -m app.migration --file path/to/other.xlsx
   uv run python -m app.migration --dry-run  # parse and validate only, no database writes
   ```

   The command prints rows read, created / updated / unchanged, every rejected row with its reason, warnings,
   and a month-by-month reconciliation against the workbook. Exit code 0 = imported and reconciled,
   1 = failed, 2 = imported but not reconciled. Running it again never duplicates data.

3. Start the app (`docker compose up --build`, or run `uvicorn` and `npm run dev` as above) and open
   <http://localhost:5173> for the forecast grid (sign-in is required since Milestone 2, see below).
   Click a project name for its details. The API is at <http://localhost:8000/docs> under `/v1`
   (projects, months, monthly totals, import results, edits, history); every `/v1` route requires a
   signed-in, provisioned user.

Assumptions, the PRD fiscal-year discrepancy and data-quality findings are in
[docs/milestone-1-assumptions.md](docs/milestone-1-assumptions.md).

Backend tests need PostgreSQL running (`docker compose up -d db`). They create and use a separate
`chosen_one_test` database on the same server and never touch the development data. The test that imports
the real workbook is skipped when `reference/` is absent.

## Milestone 2: sign in and edit

1. **Configure Google sign-in** in `backend/.env` (copy from `backend/.env.example`; never commit it):
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `SESSION_SECRET`. The redirect URI
   must be registered in Google Cloud exactly as written (`http://localhost:8000/auth/google/callback` by
   default). Without credentials the app shows a "Google sign-in has not been configured" message.
2. **Apply migrations** (`uv run alembic upgrade head`; this adds the Google identity, import baseline and
   append-only audit changes and keeps existing data).
3. **Provision yourself** (nobody can sign in until provisioned; run from `backend`):

   ```powershell
   uv run python -m app.provisioning grant --code AC --email <your-google-email> --role "Client Partner"
   ```

   `--code AC` adopts the imported Avery Chen, so the projects she owns become yours to edit. For
   leadership/admin, use `--name "Your Name" --role "CRO / Sales Leadership"` instead of `--code`. Other
   commands: `list`, `revoke`. Details and the role matrix: [docs/milestone-2-notes.md](docs/milestone-2-notes.md).
4. Open <http://localhost:5173>, click **Sign in with Google**. Double-click a month cell on a project
   you own (or open a project and edit its months), and see the entry under **Change history**.

Backend tests mock Google entirely; no live sign-in or network access is needed to run them.

## Milestone 3: HubSpot pipeline

HubSpot deals appear as probability-weighted pipeline next to committed revenue (read-only integration;
details and rules in [docs/milestone-3-notes.md](docs/milestone-3-notes.md)).

1. Apply migrations: `cd backend` then `uv run alembic upgrade head` (adds the pipeline tables; existing
   data is kept).
2. Import the Chosen One workbook first if you have not (`uv run python -m app.migration`), so there are
   forecast months and customers to match.
3. Sync the supplied HubSpot export (safe to repeat; the default path is
   `reference/03-demo-data/HubSpot_OnDeck_Dummy_2026-10-01.xlsx`):

   ```powershell
   uv run python -m app.pipeline sync          # add --file path/to/export.xlsx for another export
   uv run python -m app.pipeline stages        # the probabilities in use
   ```

4. Open <http://localhost:5173>, sign in, and choose the **Pipeline** tab: committed / weighted / combined
   totals, the monthly table, a collapsed line chart, filters (owner, TECH, region, industry, category,
   customer), deal details, and drafts awaiting confirmation. Sales leadership and Administrators also get a
   **Sync now** button; with Docker Compose it reads the mounted export.

HubSpot settings are in `backend/.env.example` (`HUBSPOT_SOURCE`, `HUBSPOT_WORKBOOK_PATH`,
`HUBSPOT_ACCESS_TOKEN` for the future live adapter, `DRAFT_CONFIRMATION_BUSINESS_DAYS`,
`HUBSPOT_STALE_AFTER_MINUTES`).

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

