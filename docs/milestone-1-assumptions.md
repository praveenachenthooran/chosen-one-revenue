# Milestone 1 assumptions and data notes

## Fiscal calendar discrepancy (PRD)

The PRD contradicts itself on the fiscal year:

| Source | Statement |
| --- | --- |
| PRD section 1 and the FR-06 grid requirement | fiscal year **April - March** |
| PRD section 9, "Assumptions" | fiscal year **July - June** ("matches the current sheet") |
| `Chosen_One_Dummy_Data_FY27.xlsx`, README sheet | "BIG FY runs April - March (FY27 = Apr 2026 - Mar 2027; FY28 starts Apr 2027)" |

**Decision for the demo:** April - March, because the supplied workbook (the actual data) and PRD
section 1 agree. The July - June sentence was not used. `backend/app/fiscal.py` is the single place that
encodes the rule (change `FISCAL_YEAR_START_MONTH` if Finance rules otherwise). Please have Finance
confirm before cut-over.

The workbook's 24 month columns (Oct 2025 - Sep 2027) span FY26 (Oct 2025 - Mar 2026), all of FY27 and
the first half of FY28. The grid groups columns by fiscal year accordingly. Months before the workbook's
"As of" date (2026-10-01) are flagged as closed months; the workbook itself states Oct 2025 - Sep 2026 are
closed (actual) months, but they are stored as forecast revenue exactly as supplied. They are not copied
into the `monthly_actuals` table.

## Workbook structure that the importer relies on

Located by header name, not by position:

- `Forecast` sheet: header row 2; project rows 3-113 (111 rows); row 114 is blank and skipped; row 1 holds
  `SUBTOTAL` formulas that are *not* project data.
- Month columns are the date-typed headers (24, all first-of-month).
- `Row ID` (R1001-R1111) is the stable identity used to prevent duplicates on re-import.
- `Users` sheet supplies owner codes (AC, JM, Shah, AF, ...). `Client Partner`, `AE` and `Commercial Owner`
  cells hold those codes.
- "N/A" in an owner column means *unassigned*. **"NA" in Region is a real value (North America)** and is
  preserved.
- Not imported in Milestone 1: `Snapshot_2026-09-01`, `Change_Log`, `Billing_Calendar`, `Reason_Codes`,
  `Demo_Stories`, `Summary` (the last is only read for Excel's cached total) and the HubSpot workbook.
  The tables to hold snapshots, audit history and HubSpot deals exist, but nothing writes to them yet.

## Validation policy

A row is **rejected** (not imported at all) when it has: a blank Customer / Project / Row ID / Status; any
monthly cell that is text (e.g. "N/A"), negative, a boolean, more than two decimal places, or a formula;
a duplicate Row ID; the same customer + project as an earlier row; or a category other than EE / EN / NN.
Amounts are never rounded, coerced or evaluated. A **warning** (row imported, flagged) is raised for
missing Client Partner, owner codes not on the Users sheet, blank TECH / Region / Industry / Category,
unexpected status, no revenue at all, and project names that are near-duplicates of another project for
the same customer. Blank monthly cells are stored as *no row*, never as zero.

## Results for the supplied workbook

111 rows read, 111 imported, **0 rejected**, 3 warnings:

| Row ID | Warning |
| --- | --- |
| R1003 (Cedarfall Energy - Data Platform Build) | no Client Partner ("N/A") |
| R1014 (Ironleaf Manufacturing - Boomi Integration Support) | no Client Partner (blank) |
| R1010 (Lakemoor Pharma - "Clinical Workflows") | near-duplicate of R1009 "Clinical Workflow"; likely a double entry that the AI QA agent / owner should resolve |

Both the importer's own sum of the source cells and Excel's cached `SUBTOTAL` / `Summary` totals agree with
the database to the cent in all 24 months (grand total $58,499,798.00).

## Other assumptions

- **Currency:** the workbook has no currency column. `projects.currency` exists (PRD FR-05) but is left
  empty, and the UI shows a plain "$" without claiming USD or CAD. UK and India rows are therefore not
  converted. Needs a Finance decision (PRD open question on reporting currency).
- **Users:** imported people are reference data only. `users.is_provisioned` is false and the
  `example.com` emails are not treated as Google Workspace identities. Provisioning arrives with SSO.
- **Categories:** EE / EN / NN descriptions come from PRD section 9, which marks them "[confirm]".
- **Import runs are logged** (`import_runs`, `import_issues`, `import_reconciliation`) so the latest result
  is visible through the API. A repeat import over an unchanged workbook updates nothing and writes no
  audit events; a changed source cell updates in place and writes an old/new `revenue_revisions` event.
  Projects missing from a later workbook are left alone (never deleted).
- **Attention ordering:** "needs attention" is derived only from the latest import's warnings. Richer
  signals (stale rows, forecast-vs-actual mismatch) need data that arrives in later milestones.
- **Grid totals row** shows the backend total for *all* projects; it does not recalculate when the
  grid's column filters change.
- **Local-only API (superseded in Milestone 2):** in Milestone 1 `/v1/*` answered 404 unless `APP_ENV` was
  `development` or `test`, because there was no authentication. Milestone 2 replaces that gate with
  sign-in on every route, in every environment (see `milestone-2-notes.md`).
