# Milestone 3: HubSpot pipeline, weighting and Closed Won drafts

HubSpot deals now flow into the forecast as a separate, probability-weighted pipeline line, shown
next to committed revenue and combined with it. The integration is **read-only**: the app only ever
reads deals; nothing is written to HubSpot.

## Where deals come from

`DealSource` (`backend/app/pipeline/source.py`) is the seam: anything that can return a
`SourceSnapshot` (deals, stage probabilities, explicit deal-to-project links, and whether the feed
is complete) can feed the sync.

* `WorkbookDealSource` reads the supplied export (`On_Deck` and `Recent_Closed` tabs, plus
  `Stage_Probabilities` and `Deal_ChosenOne_Map`). Columns are matched by header name.
* `HubSpotApiSource` is a placeholder for the future live adapter. It raises a clear
  "not implemented" error (and never makes a request). When built it must use only read (GET)
  calls with a read-only private-app token (`HUBSPOT_ACCESS_TOKEN`).

Select with `HUBSPOT_SOURCE` (`workbook` | `api`). Nothing is invented: a missing amount, start date,
duration or owner stays missing and becomes a finding.

### Field mapping (workbook column -> meaning)

| HubSpot column | Used as |
| --- | --- |
| Record ID | deal key (`hubspot_deal_id`); repeat syncs update in place |
| Deal Name, Deal Stage, Deal owner | name, stage, owner (matched to a known person by name, ignoring case and spacing) |
| Associated Company (Primary) / Associated Company IDs | company name / company ID; when the name column holds a numeric ID (the real export does this for Ravenscar) the name is treated as unknown and the deal is identified by ID |
| Amount in company currency | amount, kept exactly (up to 4 decimals, as exported). No currency column exists, so currency is left empty |
| Close Date | `YYYY-MM-DD HH:MM`, read as UTC (the export states no zone) |
| TECH / Region / Expected Start / Duration (proposed) | practice, region, phasing inputs |

HubSpot has **no industry or category field**. The industry filter therefore matches a deal only
when its company is an existing Chosen One customer whose projects agree on one industry; the
category filter (EE/EN/NN) matches only projects, so deals drop out when it is set. Both are noted in
the UI behaviour below rather than guessed.

## Weighting (PRD section 6, FR-08)

`weighted amount = amount x stage probability`, spread **evenly** over `duration` months starting in
the month of the expected start date (the day of the month is ignored). Money is `Decimal` throughout
and each deal's months are whole cents that add up exactly to its weighted amount (the remainder cents go
to the earliest months), so totals never drift from the deals behind them.

* PRD acceptance example, verified: $120,000 at 50% from Nov 1 for 6 months = **$10,000 a month,
  November to April**.
* Verified against the workbook's own formula sheet (`Weighted_Preview`): every month of Oct 2026 to
  Sep 2027 agrees to within 5 cents (cent rounding on fractional amounts).
* Pipeline is shown in open months (from the import's as-of month onward). Weighted amounts that
  fall before that, or after the last forecast month, are reported as "outside the months shown"
  instead of disappearing. Deals with no amount/start/duration are "not phased": their weighted amount is
  reported separately and kept out of the monthly figures.
* Stage probabilities and the forecast-stage rule live in `hubspot_stages`. They are **seeded once from
  the export** (the workbook marks them "Placeholder - confirm with Sales Ops") and are never overwritten by
  a sync. Administrators change them in the app (Pipeline tab > Stage probabilities); each change is
  logged. The forecast-stage rule (`in_forecast`) changes take effect at the next sync.
* Overriding the phasing of a single deal (a PRD "Could") is not part of this milestone.

## Deal lifecycle

Recomputed from the stage at every sync; each transition is written to the append-only
`pipeline_events` log (the database rejects UPDATE/DELETE on it).

| Situation | Result |
| --- | --- |
| Open stage at or beyond the forecast stage | `weighted`: counts at the stage probability |
| Below the forecast stage, unknown stage | `excluded`; if it had been weighted, the line is removed and logged |
| Closed Lost | `closed_lost`: weighted line removed and logged |
| No longer returned by a *complete* feed | `excluded` (`absent_from_source`), logged. A partial feed never removes anything |
| Closed Won, already linked to a project (explicit `Deal_ChosenOne_Map` row) | `linked`: counted once, as committed; no draft, no weighted line |
| Closed Won, no project | draft project created (below); counts as pipeline at 100% until confirmed |
| Closed Won but no draft can be made (company unknown, or that customer already has a project with the same name) | no draft, a finding, and it keeps counting as pipeline so the deal does not vanish |

**Never counted twice (FR-14/15).** A deal contributes weighted revenue only if it has no linked
*committed* project. Drafts are excluded from committed totals, so while a draft is unconfirmed the deal
counts once (as pipeline) and after confirmation counts once (as committed). The two states switch in one
transaction. The combined total is identical before and after confirming (tested).

## Closed Won -> draft -> confirmation

On sync, a Closed Won deal without a project gets a **draft project** (`status = Draft`, id `HS-<deal id>`),
linked to the Deal ID, with a *proposed* schedule: the deal amount at 100% spread from the expected start
(months beyond the forecast are reported, not added). The owner is the deal owner when they match a known
person, taking the ownership role they actually hold (AE, else Client Partner, else Commercial Owner);
otherwise the draft has no owner and only Sales leadership or Administrators can confirm it.

A `draft_confirmations` row requests confirmation, due in **2 business days** (weekdays only; no
holiday calendar). There is no email/Slack service in the prescribed stack, so "notify" means: the owner
sees it under *Awaiting confirmation* (Pipeline tab); once overdue it is flagged and logged
(`draft_overdue`, once) for the owner and Sales leadership, who can see all drafts.

Confirming (`POST /v1/pipeline/drafts/{project_id}/confirm`) needs the edit rights for that project, a
non-empty schedule (edit the months with the normal editor first), and, when the customer already has
committed projects, an explicit acknowledgement that this is new work. Optionally set category and
industry. It runs in one transaction: status Draft -> Active, audit events (who, old/new), weighted line
retired. If the schedule differs from the deal amount by more than 10% (allowing for months outside the
forecast) the response and UI flag it; this does not block (the PRD raises it as a QA finding, which
arrives with the AI QA milestone). A merge-with-existing-project flow is not built; the acknowledgement is
the control for now.

## Findings

Raised per sync (stored in `hubspot_sync_findings`, shown on each deal): missing amount, start or duration;
invalid values; company name is an ID; unknown stage; owner missing/unresolved/ambiguous; **possible
duplicate deals** (same company, amount, stage and close *date*, as the real export's Corriveau pair);
**stale close dates**; blocked drafts; links to unknown projects; a won deal that reopened with an
unconfirmed draft.

## Sync

* `uv run python -m app.pipeline sync [--file path]` or **Sync now** in the Pipeline tab (Sales
  leadership and Administrators; `POST /v1/pipeline/sync`). `uv run python -m app.pipeline stages` shows the
  probabilities in use.
* One sync is one transaction and is **idempotent** (deals keyed by Record ID; a second run over unchanged
  input creates and changes nothing, including drafts and links). Two syncs cannot run at once (409).
* **Failures never block the forecast.** Any failure rolls the whole sync back, records a failed run, and the
  API answers 502 with a clear message; the last good data and every committed view are unchanged. The
  Pipeline tab shows **"Sync stale since ..."** when the latest run failed, or the last good sync is older than
  `HUBSPOT_STALE_AFTER_MINUTES`. A background schedule (the PRD's hourly sync) needs the scheduled-jobs
  milestone; until then syncs run on demand.
* A sync only ever *creates* draft projects. It never edits existing project revenue or baselines, and never
  overwrites in-app edits (tested).

## Authorization

Everyone signed in can read the pipeline. Sync: Sales leadership, Administrator. Stage probabilities:
Administrator. Confirm a draft: whoever may edit that project (its owners, Sales leadership,
Administrator); a refusal names the owners. All checks are enforced in FastAPI, with CSRF on every
mutation.

## Forecast API additions

* `GET /v1/forecast/monthly` - committed, weighted and combined totals by month and fiscal year, plus the
  unphased and outside-the-months amounts. Filters (PRD FR-16): `owner`, `practice` (TECH), `region`,
  `industry`, `category`, `customer`, applied to both halves. `GET /v1/revenue/monthly-totals` (committed)
  now also excludes unconfirmed drafts.
* `GET /v1/pipeline/deals`, `/deals/{id}`, `/filter-options`, `/stages`, `/sync/status`, `/confirmations`;
  `PUT /v1/pipeline/stages/{id}`; `POST /v1/pipeline/sync`; `POST /v1/pipeline/drafts/{project_id}/confirm`.

## Findings from the supplied data

53 deals (48 open, 5 recently closed): 2 explicit links (R1007, R1003), 1 draft created (Brightharbor), 3 deals
whose company is an ID, 2 that cannot be phased, 6 stale close dates and 1 look-alike pair.
