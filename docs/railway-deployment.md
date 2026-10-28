# Deploying to Railway

One Railway project per the Technology Guide: the app, PostgreSQL and the scheduled jobs. GitHub `develop`
deploys the development environment and `main` the production demonstration. I could not deploy from this
environment (no Railway access); this is the configuration to apply. Nothing secret is committed.

## Services

| Service | Source | Start command | Schedule |
| --- | --- | --- | --- |
| **app** | this repo, root `Dockerfile` (its last stage, `production`, is the default build target; it builds the React app and serves it from FastAPI) | the image default (`uvicorn ... --port $PORT`) | always on |
| **postgres** | Railway PostgreSQL plugin | n/a | n/a |
| **cron-nightly-qa** | same repo and Dockerfile | `/opt/venv/bin/python -m app.qa nightly` | `0 2 * * *` (daily, 02:00 UTC) |
| **cron-monthly-snapshot** | same | `/opt/venv/bin/python -m app.snapshots scheduled` | `0 6 * * 1-5` (every weekday 06:00 UTC; acts only on or after the month's first business day, once a month) |
| **cron-hubspot-sync** | same | `/opt/venv/bin/python -m app.pipeline sync` | `0 * * * *` (hourly; only once the live HubSpot adapter exists. Until then it reads the workbook, so leave it off) |

Each cron service uses the same image and the same variables as the app (use shared variables / reference
variables). A cron job exits `0` on success or a harmless skip, and `1` on failure, so Railway shows failed runs.
All three jobs are safe to run more than once.

**Pre-deploy command** on the app service (runs migrations before each release):
`/opt/venv/bin/alembic upgrade head`

**Health check path**: `/health/ready` (checks the database).

## Variables

Set on the app and on each cron service. Secrets go in Railway Variables, never in Git or the image.

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` (or `staging` for develop). Turns on secure cookies and requires a real session secret |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}`. Railway supplies `postgresql://...`; the app rewrites it to the `asyncpg` driver itself |
| `SESSION_SECRET` | 32+ random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `FRONTEND_URL` | the public URL of the app service, e.g. `https://chosen-one.up.railway.app` (CORS origin) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | the BIG Google Cloud OAuth client (Web application, Internal) |
| `GOOGLE_REDIRECT_URI` | `https://<app domain>/auth/google/callback`, registered with Google exactly |
| `GOOGLE_HOSTED_DOMAIN` | optional, your Workspace domain |
| `ANTHROPIC_API_KEY` | the approved enterprise key. Leave **unset** until you have one: the detective then uses its deterministic investigator |
| `AI_DATA_USE_APPROVED` | `true` only once the enterprise data-use terms cover sending forecast data to Claude. Without it Claude is never called, even with a key |
| `ANTHROPIC_MODEL` | optional, default `claude-sonnet-5-5` |
| `AI_MONTHLY_CALL_LIMIT` | optional cost ceiling, default `200` investigations a month |
| `HUBSPOT_SOURCE`, `HUBSPOT_WORKBOOK_PATH`, `HUBSPOT_ACCESS_TOKEN` | see Milestone 3. The live adapter is not built; the workbook is not in the image, so a hosted app has no deal source until it is |

`QA_STALE_DAYS`, `QA_MISMATCH_PERCENT`, `QA_ANOMALY_MIN_AMOUNT`, `QA_ANOMALY_MIN_PERCENT` default to the PRD values
and normally need no change.

## First deploy checklist

1. Create the Postgres plugin and the services above; set the variables.
2. Deploy `develop` first. Confirm `/health/ready` is green and the pre-deploy migration ran.
3. Load data once (a one-off command in the app service): `/opt/venv/bin/python -m app.migration --file <path>`
   (the workbook must be supplied to the service, e.g. mounted or uploaded; it is deliberately not in the image).
4. Provision users: `/opt/venv/bin/python -m app.provisioning grant --email <email> --role Administrator`.
5. Run `/opt/venv/bin/python -m app.qa run` once and open the **Quality** tab.
6. Enable the three cron services.
7. Add the Railway domain to Google's authorized redirect URIs and sign in.

## Known gaps

* The reference workbooks are not in the Docker image, so data loading and the HubSpot workbook source need the
  files supplied to the service. The live HubSpot adapter is not implemented.
* Google sign-in has not been exercised against a real Google project from this environment.
