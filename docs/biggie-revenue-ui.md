# Biggie Revenue: interface, roles and open decisions

The user-facing product is **Biggie Revenue** (it was "The Chosen One"). Database tables, API routes and backend
modules keep their names. This note records what the redesign does, the rules behind it, and every place where the
sources disagree or are silent, so none of it is decided by accident.

Sources: `reference/02-product-design/biggie-revenue-reference.html` (visual specification),
`reference/02-product-design/Team_Feedback.md` (Michael's chart and workflow requirements), the PRD and Technology Guide.

## Design system

Tokens are lifted from the reference HTML into `frontend/src/styles.css` and used everywhere: teal brand `#0D6B72`,
Schibsted Grotesk, 236px sidebar, 12px panels, 38px controls, RAG-style status pills, a divided KPI strip, one filter
bar, one table style, one legend style for every chart. Light and dark themes follow the OS setting, as in the reference.
Bootstrap is still loaded for the older secondary screens (Pipeline, Snapshots, Actuals, Accuracy, Checks, Monthly grid);
`styles.css` re-themes it, so those screens share the same look.

Charts use Chart.js. Their colours are read from the CSS tokens at draw time. Every plotted number comes from an API
response; nothing is hard-coded.

The favicon is the hex mark from the reference HTML. No other logo was introduced.

## Three role experiences

Landing pages are presentation only. Edit rights, history access and who may run checks are decided by the server's
existing permission model and were not changed. `frontend/src/roles.ts` picks the landing page:

| Role | Lands on | Notes |
| --- | --- | --- |
| Client Partner | **My projects** | Only projects they own. No graph on the list. |
| Commercial Owner | **Portfolio** | Their projects, with a toggle to see all projects read-only. |
| CRO / Sales Leadership | **Revenue overview** | Company-wide. |
| Administrator | Revenue overview and Portfolio | |
| Finance, Seller / AE, Delivery / Resourcing | **All projects** | Read-only portfolio, as before. |

Everyone also gets **Reports and checks**: Pipeline, Checks, Snapshots, Monthly grid, and (Finance, Sales leadership,
Administrator) Actuals and Accuracy. The Monthly grid is the previous spreadsheet-style editor, kept for people who
want every project by month.

### Client Partner: My projects

Columns: Project, Status, Owner, Revenue at stake, Required action, Priority. Ordered by urgency. Filters: search,
Status, Customer, Owner, Practice, Month. Technology, industry, region and mismatch columns were removed (Michael).
Opening a project shows the summary, what needs attention, the Revenue Detective, recommended actions, the editable
monthly forecast, a collapsed line graph and the change history.

### Status rules (no score, nothing subjective)

Implemented in `frontend/src/projects/status.ts` and covered by `status.test.ts`.

* **Missing Entry**: an active project whose forecast for the current month is blank (an explicit 0 is not blank), or a
  still-detected "missing required fields" finding.
* **Off Track**: any other still-detected finding of warning or error severity.
* **On Track**: neither of the above.
* **Priority**: High for an error or Missing Entry, Medium for a warning, Low otherwise.

A finding counts as *still detected* while it is open, or while it is accepted or dismissed **and** the latest
successful check run still detects the same condition (the engine sets `last_seen_at` to that run's start time).
Accepting or dismissing a finding therefore never turns a project green. It turns On Track only once the checks no
longer detect the condition.

### Commercial Owner: Portfolio

KPI strip, one filter bar (Customer, Practice, Region, Month, search), a project table, and a collapsed line graph of expected
monthly revenue for the projects shown. Excel and CSV exports use the existing server export with the same filters.
Project pages show graph, editing (only where `can_edit`), history and findings. Projects the owner cannot edit are
labelled "View only".

### Sales Leadership: Revenue overview

Filters: Practice, Region, Customer, Owner, Industry, Category. KPIs: committed revenue, weighted pipeline, combined
forecast, revenue at risk. The main chart is Michael's graph (below). Also: fiscal-year outlook, near term versus
later months, forecast changes against a saved snapshot (the server's snapshot totals under the same filters), and
the largest open exceptions, each opening the Revenue Detective.

## Michael's chart requirements

From `Team_Feedback.md`:

* CRO / Sales leadership: "a nice big bar graph", and for the big picture "an estimated (with currently running
  projects), actual (of current projects), and potential (projects that aren't fully accepted yet but could increase
  our overall revenue)". Implemented as **grouped bars by month with three series**: Estimated = committed forecast,
  Actual = uploaded actuals, Potential = probability-weighted HubSpot pipeline.
* Client Partner and Commercial Owner: remove the graph from the top, put it below and collapsed, **line graph, not
  bars**. Implemented as line charts placed below the content.
* Each project graph: show the original SOW/import; show each later edit; show extra months or scope as a new line.
  Implemented as **Original import** (dashed), **Current forecast**, and **Added after import** (months that did not
  exist in the import). The gap between the first two lines is every edit. The data is `monthly_revenue.baseline_amount`
  against `amount` from the project API.

### Choices made where the sources are silent or conflict

1. **Bars versus lines for the three series.** The notes say "bar graph" for leadership and do not say how the three
   series are drawn. Three grouped bar series is the literal reading. If Michael meant bars for two series and a line
   for the third, only `BarChart.tsx` changes.
2. **Graph placement versus the brief.** The product brief asks for a graph-heavy Commercial Owner view and a graph in
   the Client Partner's project detail. Michael asks for graphs below and collapsed. Resolution (confirmed by the
   product owner): graphs sit *below the table and collapsed by default* for Client Partners and Commercial Owners; the
   leadership overview keeps its large chart open.
3. **Per-project actuals.** Uploaded actuals are company-wide by month, so no per-project actual line is drawn. Adding
   one needs project-level actuals data.
4. **Longer-term forecast.** Months after the next three are drawn lighter and labelled "less certain" in the tooltip.
   There are no confidence scores, bands or predictive models.
5. **Profit.** Not shown. There is no approved cost data.

## Revenue Detective

Hierarchy: title and severity, affected project, revenue at stake, summary, **Confirmed**, **Hypothesis**,
**Insufficient evidence**, recommended actions, the reviewer's decision, then **View evidence** and **History** on
request. Evidence is shown as readable records (`quality/evidence.ts`), never JSON; the investigation steps are in plain
words. Source references stay behind each item's "Sources". The agent remains read-only; accepting a finding records a
decision and changes no revenue.

## Open decisions and conflicts (need a business answer)

1. **Fiscal year start.** The workbook and PRD section 5 say April to March; the latest meeting says September. The
   backend now reads `FISCAL_YEAR_START_MONTH` (default 4). Labels are computed on the fly, so changing it rewrites no
   stored month or snapshot, but it will relabel *existing* months and old snapshot exports under the new calendar.
   Left at April until the business confirms.
2. **Currency.** The meeting says USD. The workbook carries no currency, so amounts show a plain `$` and the product
   does not claim USD or convert anything.
3. **Re-running validation.** Only Sales leadership, Finance and Administrators can run the checks. A Client Partner
   can investigate and review findings on their own projects but cannot re-run validation; the page says checks re-run
   nightly and who can run them now. Changing this is a permission decision, not a UI one.
4. **SOW / PDF upload.** Not built and not shown. Spreadsheet import is the supported input.
5. **Forecast changes for Commercial Owners.** The leadership overview compares against a snapshot. The Commercial
   Owner portfolio links to Snapshots rather than repeating that comparison.

## Demo walkthrough

Sign in with Google as each person. The accounts must already be provisioned with the matching role. Click paths use
the labels in the interface.

**Client Partner.** My projects → open the top *Off Track* row → read *Needs attention* → **Open Revenue Detective** →
**Investigate** → read the summary, Confirmed, Hypothesis and Recommended actions → **View evidence** → enter a reason
and **Accept finding** or **Dismiss finding** → close → in *Monthly forecast* correct the flagged month and press
Enter → the status stays Off Track until the checks stop detecting the condition. Re-running is done by Sales
leadership or Finance (decision 3).

**Commercial Owner.** Portfolio → read the KPIs → filter (Customer or Practice) → open a project → open *Monthly
revenue* → edit a month and press Enter → the graph's *Current forecast* line moves to the saved value → back to the
Portfolio → **Export Excel**.

**Sales Leadership.** Revenue overview → compare Estimated, Actual and Potential in *Revenue by month* → note the
shaded *Next 3 months* → read *Near term and beyond* and *Fiscal-year outlook* → pick a Region or Practice → in *Forecast
changes* pick a snapshot and read the movements → in *Risks and exceptions* **Open Revenue Detective** → **Open
HubSpot deal** or **View snapshots** for the evidence.

## Visual QA

Reference screenshots and the app's screenshots are taken with Playwright at 1440 × 900, 1920 × 1080 and 1024 × 768
into `qa-screenshots/`, which is git-ignored because it can contain confidential data. Note for local development: on
Windows the Docker bind mount can fail to deliver file-change events to the containerised Vite server, so edits may not
appear until `docker compose restart frontend`. Running `npm run dev` in `frontend/` natively avoids that.

## Demo-day notes

* **Do not click "Run checks now" on camera unless you want 87 extra warnings.** The demo data is dated 2026-10-01
  and the earlier QA runs were pinned to that date. A live run uses today's real date, so about 87 more rows cross the
  30-day "not updated recently" threshold and the Checks page jumps from 17 to 104 open findings. Revalidation itself
  works. To restore the demo state afterwards, run the same command the earlier runs used (nothing is deleted; the
  engine marks the extra findings as no longer detected):
  `docker compose exec backend /opt/venv/bin/python -m app.qa run --as-of 2026-10-01`
* **Actuals on record are a single $12,345 test upload for Oct 2026.** The grouped bar chart's *Actual* series is
  therefore almost invisible next to the $2.7M estimate. Upload a realistic actuals file before recording if the
  Actual bars matter.
* **Investigations call Claude** when the AI key and data-use switch are on, and take roughly 20 to 40 seconds.
* Accepting or dismissing a finding is permanent for that finding, so rehearse on a finding you do not need.
