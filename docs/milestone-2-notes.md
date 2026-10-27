# Milestone 2: authentication, permissions and editing

## Signing in

Google Workspace OpenID Connect through Authlib (authorization-code flow with PKCE). Authlib keeps the
OAuth `state`, `nonce` and PKCE verifier in the signed session and itself rejects a callback with a
mismatched state, or an ID token with a bad signature, audience or nonce. The redirect URI is taken from
`GOOGLE_REDIRECT_URI`, never from the request.

A Google identity is accepted only if it is **verified** (`email_verified`) and then matched to an
**explicitly provisioned** application user:

1. a user already bound to that Google `sub` is matched on `sub` alone (an email change at Google does not
   matter, and someone else's address cannot take the account over);
2. otherwise an unbound, provisioned user is matched on the verified email once and bound to that `sub`.

Nothing is granted for an email domain. `GOOGLE_HOSTED_DOMAIN` is an optional *extra* restriction (the `hd`
claim) and still requires provisioning. People imported from the workbook have `is_provisioned = false`
and their `example.com` emails are never trusted. Imports and re-imports never touch a provisioned person's
email, name or roles.

Sessions are a signed, HttpOnly, `SameSite=Lax` cookie (`Secure` when `APP_ENV` is staging or production),
expiring after `SESSION_MAX_AGE_SECONDS` (default 8 h, absolute). The user is re-read from the database on
every request, so revoking provisioning ends access immediately. Each sign-in starts a fresh session (no
fixation) with a new CSRF token. State-changing requests (`PUT` edits, `POST /auth/logout`) must send that
token in `X-CSRF-Token`. Hosted environments refuse to start without a 32+ character `SESSION_SECRET`.

## Provisioning

```powershell
cd backend
uv run python -m app.provisioning list
# Adopt a person from the workbook (so the projects they own apply to them):
uv run python -m app.provisioning grant --code AC --email <their-google-email> --role "Client Partner"
# Or create someone new:
uv run python -m app.provisioning grant --name "Pat Finance" --email <their-google-email> --role Finance
uv run python -m app.provisioning revoke --email <their-google-email>
```

`grant` sets exactly the roles given (valid: `Seller / Account Executive`, `Client Partner`,
`Commercial Owner`, `CRO / Sales Leadership`, `Finance`, `Delivery / Resourcing`, `Administrator`; the
workbook spellings `Seller / AE`, `Sales leadership`, `Resourcing (view only)` and `Admin` are accepted).
No addresses are built in; you supply them. A full admin screen is a later milestone.

## Permission matrix (PRD FR-03)

| Role | View forecast | Edit months | View change history |
| --- | --- | --- | --- |
| Seller / Account Executive | all projects | projects where they are the assigned AE | those they can edit |
| Client Partner | all projects | projects where they are the assigned Client Partner | those they can edit |
| Commercial Owner | all projects | projects where they are the assigned Commercial Owner | those they can edit |
| CRO / Sales Leadership | all | **all projects** | all |
| Finance | all | none (read-only) | all |
| Delivery / Resourcing | all | none (read-only) | none |
| Administrator | all | **all projects** | all |

An ownership assignment only grants edit rights to someone who also holds the matching role (a Finance-only
person who is named as an owner still cannot edit). A refused edit answers `403` and names the owners.
Rules live in `backend/app/auth/permissions.py` and run in FastAPI on every request; the UI only mirrors
them (`can_edit` on each project).

Decisions to confirm with the business:

- All provisioned users can *view* every project (PRD FR-03: other roles are "read-only", not "hidden").
  Dashboards that default to "my projects" are a later milestone.
- "Commercial Owner: signed projects" is not yet distinguishable (draft/closed-won projects arrive with
  HubSpot), so every imported project counts as signed.
- Closed (past) months are editable and audited. Month locking belongs with Finance's close process.
- Change history is limited to people who can edit the project, plus Finance, leadership and admins.

## Editing and audit

`PUT /v1/projects/{id}/revenue/{YYYY-MM-01}` with `{ "amount", "expected_amount", "reason"? }`. In order:
authenticate, CSRF, project exists, permission, validate, concurrency check, write. Amounts must be plain
non-negative numbers with at most two decimals and at most 12 integer digits; text, formulas, exponents,
`NaN`, negatives, booleans and non-ASCII digits are rejected, and nothing is evaluated. Money is stored and
calculated as `NUMERIC(14,2)` / `Decimal`. Blank is not zero: enter `0` for no revenue.

**Concurrency:** `expected_amount` is the value the editor last saw (`null` for a blank month) and is
required. If the stored value differs, the API answers `409` with the current value and writes nothing. The
project row is locked for the transaction, so two simultaneous edits cannot both win.

**Audit:** the value change and its `revenue_revisions` row (project, month, old and new value, user id,
display name, timestamp, optional reason) are committed in one transaction. A trigger makes
`revenue_revisions` append-only at the database level, so even direct SQL cannot update or delete events. An
edit that does not change the value records nothing.

**Baseline vs current:** each month has `amount` (the current forecast, editable) and `baseline_amount` (what
the Excel import loaded, never edited by users). `GET /v1/revenue/monthly-totals?basis=baseline` reproduces
the imported figures at any time; the default `basis=current` includes edits. Import reconciliation compares
the workbook with the baseline, so it stays reproducible after edits. Re-importing the workbook updates
months nobody edited, never overwrites an in-app edit (it logs an `edited_in_app_kept` warning), and
reports unchanged workbooks as unchanged. Snapshots (a later milestone) are separate immutable copies, so
edits cannot change them.

## Not verified live

Google sign-in has been exercised only with mocked Google responses plus Authlib's real state, redirect
and PKCE handling. Live verification needs the BIG OAuth client credentials in `backend/.env` and the
redirect URI registered in Google Cloud (see `backend/.env.example`).
