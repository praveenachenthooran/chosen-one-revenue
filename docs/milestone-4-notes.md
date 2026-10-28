# Milestone 4: snapshots, exports, actuals and accuracy

This milestone adds the "month-start snapshot, export, load actuals, report variance" part of the
PRD's primary journey (FR-19 to FR-24, US-12 to US-14). Nothing from Milestones 1 to 3 changed
behaviour; the only edit to an existing screen is the new **Post-snapshot change** flag in a project's
change history, and Export links on the Pipeline tab.

## PRD points that were unclear, and what was done

| Question | What the PRD says | What this implementation does |
| --- | --- | --- |
| **Fiscal months** | Section 5 says fiscal year April to March; section 9 ("Assumptions") says July to June | April to March, as in the workbook and Milestones 1 to 3 (`app/fiscal.py`). FY27 = Apr 2026 to Mar 2027. Exports state this. |
| **"First business day"** | Snapshot taken on "the first business day of each month" | Monday to Friday, UTC. No holiday calendar exists, so a holiday on the first weekday still counts as a business day (the same rule Milestone 3 uses for the 2-business-day draft deadline). |
| **If the job misses that day** | Not stated | The job is run every weekday and takes the month's snapshot on the first run on or after the first business day, if none exists. A late run is therefore a catch-up, and the snapshot records its true `taken_at`. |
| **What "M-k snapshot" means** | "Snapshot of month M taken at M-3" | For actuals month T, the M-k snapshot is the one taken in calendar month T-k. If that month has both a scheduled and a manual snapshot, the scheduled one is used (the "month-start" snapshot); otherwise the earliest manual one. |
| **What the forecast value is** | Not stated | The combined (committed + weighted) figure for month T in that snapshot: the same number the forecast screen shows. Committed and weighted are shown separately too. |
| **Variance** | "Percentage variance", "within +/-5%" | Variance = forecast - actual; percentage = variance / actual x 100 (2 decimals). Flagged when the absolute variance is **more than** 5% of the actual (exactly 5% is within target). A zero actual has no percentage and is flagged only if something was forecast. |
| **Which look-aheads are flagged** | Target is +/-5% at M-3; M-1 and M-2 are reported | All three use the same 5% scale and flags; M-3 is labelled as the target and summarised. Months 4 to 12 (a tolerance the PRD leaves open) are not reported. |
| **Currency** | One reporting currency; GBP/India treatment is an open question | No currency exists in the workbook or HubSpot export, so there is none to convert: amounts are exported and compared as stored, in the same unit, and exports say so. |
| **Actuals mapping** | "By customer/project", file keyed to project, source still open | One row per customer or project and month (see the format below). A Project ID (the Row ID) is the strongest key; otherwise Customer plus Project name; Customer alone is a customer-level actual. Names match ignoring case. |
| **Month of an actual** | Not stated | Any date or month text is read as its month. A month in the future is rejected. |
| **Accuracy scope** | Not stated | Company total by month. Filters are not applied to the accuracy report (actuals are not carried at the same grain as every filter). |

## Snapshots (FR-19 to FR-21)

* A snapshot stores the **unfiltered** company forecast at one instant: every committed project-month and
  every weighted deal-month, with the customer, owner, practice, region, industry, category, status, stage
  and probability those rows had **then**, plus the monthly totals, what the forecast was built from (the
  import and HubSpot sync runs, the "as of" date) and the stage probabilities used. Filters are applied when
  a snapshot is *read*, to the stored lines, so any filtered historical view can be reproduced and a later
  rename, new owner or edit cannot change it (tested).
* Unconfirmed Closed Won drafts are not committed, and a deal with a committed project is never also weighted,
  exactly as in the live forecast. Deals that could not be phased (no start date or duration) have no month, so
  they are stored as a snapshot-level figure and appear only in the unfiltered view.
* **Immutable**: PostgreSQL triggers (migration 0005) reject every UPDATE and DELETE on the snapshot, line and
  month tables, and the API has no endpoint that changes one. Each snapshot also carries a SHA-256 checksum of
  its content; `Verify integrity` recomputes it from the stored rows.
* **Consistent**: the forecast is read on its own connection at REPEATABLE READ, so a snapshot is one instant even
  while people edit (and does not block them). Creation is atomic: the snapshot, its lines and its totals all
  exist or none do.
* **Scheduled**: `uv run python -m app.snapshots scheduled` (cron: weekdays; Railway cron runs the same command
  in the image: `/opt/venv/bin/python -m app.snapshots scheduled`). It takes the month's scheduled snapshot on or after the
  first business day, once. A unique index allows one scheduled snapshot per month and a lock serialises
  concurrent runs, so repeats and races create nothing extra (tested). Every run, including ones that do
  nothing or fail, is written to `job_runs` and shown on the Snapshots tab; a failed run exits non-zero.
* **On demand**: Finance and Administrators (`POST /v1/snapshots`, or `python -m app.snapshots manual`). Manual
  snapshots are unlimited and do not replace the scheduled one.
* **Post-snapshot edits**: editing a month that already appeared in a snapshot is allowed; the snapshot is
  unchanged and the edit is flagged "Post-snapshot change" in that project's history (the workbook import is not
  flagged).

## Exports (FR-22, FR-23)

* `GET /v1/forecast/export` (current view) and `GET /v1/snapshots/{id}/export`, as `format=xlsx` or `format=csv`,
  with the same filters as the screen. Both come from the same lines as the totals, so an export always matches
  the on-screen totals (tested against the live and snapshot views).
* The workbook has **Summary** (export time in UTC, who exported it, filters applied, snapshot id / taken at /
  checksum, fiscal year and currency notes), **Monthly** (committed, weighted, total, fiscal-year totals and a grand
  total), **Projects** (committed by project and month) and **Pipeline** (weighted by deal and month). A CSV is one
  table (`view=monthly|projects|pipeline`) preceded by the same facts as key,value rows and a blank line; it is UTF-8
  with a byte-order mark so Excel reads accents correctly.
* Text that a spreadsheet could read as a formula (starting with `=`, `+`, `-` or `@`) is prefixed with an
  apostrophe, so a project name can never run as a formula.
* Every export is recorded (who, current or snapshot, format, filters) in an append-only `export_events` table.

## Actuals (FR-24)

CSV or XLSX (first sheet, or a sheet named "Actuals"), header row, up to 5 MB and 20,000 rows. Column names are
matched ignoring case and punctuation:

| Column | Required | Notes |
| --- | --- | --- |
| Month | yes | `2026-09`, `2026-09-30`, `Sep 2026`, `09/2026` or an Excel date |
| Amount | yes | A number; `$` and thousands commas allowed; `(1,200)` is -1200 (credit notes). More than 2 decimals are rounded half-up to cents and reported |
| Project ID | one of | The Chosen One Row ID, e.g. R1001 |
| Customer | these two | Customer name; with Project it identifies the project, alone it is a customer-level actual |
| Project | optional | Project name |

* **All or nothing.** If any row has a problem, nothing is stored, every problem is listed with its row number and
  column, and the rejected attempt is kept in the upload history. Problems checked: unreadable or empty file, wrong
  type, missing columns, unknown project or customer, a Project ID that disagrees with the customer or project name,
  a bad month or amount, a future month, duplicate rows, and customer-level plus project-level actuals for the same
  customer and month (they would be counted twice).
* **Check file** (`dry_run=true`) runs the same validation and reconciliation and stores nothing.
* **Reconciliation report** per upload: rows read, accepted, new, replacing earlier values, unchanged, rejected;
  by-project and by-customer counts; and, for each month, the actual total beside the committed forecast now.
* **Re-uploading**: a corrected value supersedes the earlier one for the same customer/project and month; the earlier
  value stays on record (marked superseded). An identical re-upload changes nothing. A partial file never removes
  other customers' actuals.
* Who: Finance and Administrators load actuals; Finance, Sales leadership and Administrators can see uploads, actuals
  and the accuracy report. Everyone else is refused by the API (and the tabs are hidden).

## Accuracy report

`GET /v1/accuracy` and the **Accuracy** tab: for each month with actuals, the M-3, M-2 and M-1 snapshot forecasts,
variance and percentage, and a flag outside +/-5%; plus totals per look-ahead over the months that have both a
snapshot and an actual, and a summary of how many months met the M-3 target. A look-ahead with no snapshot, or whose
snapshot did not cover the month, is shown as such and left out of the totals; nothing is estimated.

## Authorization summary

| Action | Who |
| --- | --- |
| View snapshots, snapshot totals, verify, export (current or snapshot) | everyone signed in |
| Take a snapshot on demand | Finance, Administrator |
| Load actuals (including "Check file") | Finance, Administrator |
| View actuals, uploads and the accuracy report | Finance, Sales leadership, Administrator |

All checks are enforced in FastAPI; every change needs the CSRF token. The scheduled job runs as the system and
has no API.

## Data model (migration 0005)

`forecast_snapshots`, `forecast_snapshot_lines` and `forecast_snapshot_months` (reshaped from the unused
Milestone 1 stubs; the migration refuses to run if those stubs hold rows), `actuals_uploads`, `monthly_actuals`
(reshaped), `job_runs` and `export_events`. Snapshots, their lines and months, uploads and export events are
append-only (database triggers). Downgrading removes this milestone's data.

## Not in this milestone

* Months 4 to 12 accuracy ("directional") and accuracy by filter.
* Currency conversion (no currency data exists).
* A holiday calendar for "business day".
* Automatic delivery of the Railway cron job (the command is provided and documented; the schedule is configured on
  the Railway project).
* AI investigations (Milestone 5).
