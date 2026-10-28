# Milestone 5: QA engine and AI Revenue Detective

Two layers, kept apart on purpose:

1. A **deterministic QA engine** finds problems with fixed, testable rules (FR-35 onward). It is the part the PRD
   makes mandatory and the part whose accuracy is measured.
2. The **AI Revenue Detective** *explains* a finding that the engine raised. It never finds, decides or changes
   anything, and it is optional: without credentials the same screen is served by a deterministic investigator.

## The checks

Run on demand (**Run checks now**, `POST /v1/qa/runs`, or `python -m app.qa run`) and nightly
(`python -m app.qa nightly`, see [railway-deployment.md](railway-deployment.md)). Thresholds are the PRD's and are
configurable (`QA_*` variables).

| Check | Raises when | Financial impact (deterministic) | Routed to |
| --- | --- | --- | --- |
| `duplicate_project` | Same customer, names equal after ignoring case/punctuation/plurals, or >= 92% similar with at least half the shared forward months equal | forward revenue counted twice: the smaller of the two rows, summed over open months | the later row's owner |
| `closed_won_missing` | A Closed Won deal has no *confirmed* project (no draft could be made, or the draft is unconfirmed; overdue = error) | the deal amount | the deal owner |
| `amount_mismatch` | A deal's HubSpot amount differs from its linked committed schedule by **more than** 10% (allowing for months beyond the forecast) | the absolute gap | the project's owner |
| `stale_row` | An active row has revenue in the next three months and was last updated **more than** 30 days ago (latest of the workbook's "last updated" and the last in-app edit) | the revenue in the window | the project's owner |
| `mom_anomaly` | A month-over-month change in an open month of at least $5,000 **and** 30% of the prior month, with no reason code recorded for that month. Fixed-fee rows are exempt (lumpy by nature); a restart from zero is not an anomaly | the largest single movement | the project's owner |
| `missing_required` | A project lacks an owner, category, TECH, region or industry | forward committed revenue on the row | the owner; unassigned if none |
| `deal_missing_fields` | A counted HubSpot deal lacks amount, expected start or duration, so it cannot be phased | its weighted amount | the deal owner |

Currency is not checked: it is absent from every source, so requiring it would flag every row.
Routing order for projects is Client Partner, then Commercial Owner, then AE. A finding with nobody to route to is
**unassigned** and can only be decided by Sales leadership or an Administrator.

**No duplicates across runs.** Each finding has a fingerprint (check + subject) and one row for life. A repeat run
refreshes it; if the condition disappears it becomes `resolved` (automatically, with an audit event) and reopens if
the condition returns. Accepted and dismissed findings are never re-raised. Every change is in the append-only
`qa_finding_events`.

### Accuracy on the seeded dataset (PRD acceptance)

The PRD asks for a seeded set of 20 known errors and recall >= 90% / precision >= 80%. None was supplied, so
`backend/tests/qa_seed.py` builds one with its ground truth stated: 4 duplicates, 3 stale rows, 4 unexplained
movements, 4 incomplete rows, 3 Closed Won deals not committed and 2 amount mismatches, hidden among 50 clean
projects, 13 deals and several look-alike **decoys** (an anomaly with a recorded reason, a fixed-fee milestone row,
a sub-$5,000 rate uplift, a restart from zero, a completed row, similarly named but different work, and deals within
10%). The test fails if recall < 90% or precision < 80%; the rules currently score 100% / 100% on it.
Note this measures the rules against a set written alongside them, so it shows the rules do what they say, not how
they will fare on unseen real errors.

## The AI Revenue Detective

* **Tools** (typed with pydantic, read-only): `get_project_history`, `compare_snapshots`, `get_hubspot_deals`,
  `find_duplicates`, `get_project_owners`. There is no tool that writes, edits, accepts or dismisses.
* **Runs as the person who asked.** A tool returns only what that person could see: a project's change history is
  refused to someone who may not view it (tested). Unknown tools and bad arguments come back as errors, never
  exceptions.
* **Structured result:** summary, causes (each `confirmed`, `hypothesis` or `insufficient_evidence`, with evidence
  references such as `project:12`, `revision:34`, `snapshot:5`, `deal:7`), next steps (with who), confidence and
  limitations. **A cause can only stay "confirmed" if it cites a record the investigation actually retrieved**;
  anything else is downgraded to a hypothesis and invented references are dropped.
* **Financial impact is the engine's figure.** A number from a model is ignored and the UI says it is calculated.
* **Claude** (Anthropic) is used only when `ANTHROPIC_API_KEY` is set *and* `AI_DATA_USE_APPROVED=true` *and* the
  monthly call limit (`AI_MONTHLY_CALL_LIMIT`, default 200) is not reached. The model runs a tool-use loop (max 6
  rounds) and ends by submitting its report. Prompts, tool calls and outputs are stored on the investigation
  (PRD: logged). Data sent: the finding and the tool results (names of projects, customers, owners and amounts); no
  email addresses and no credentials.
* **A report must be a real explanation.** Before a submitted report is accepted it must follow at least one
  successful lookup, be submitted on its own (not in the same turn as the lookups), have a plain-language summary,
  at least one specific cause that cites a retrieved record or is marked `insufficient_evidence`, and at least one
  next step; "placeholder", empty or generic text is refused. A refused report goes back to the model as a tool
  error saying why (twice at most); if it is still unusable the deterministic investigator answers and the reason
  is stored. (Found in manual testing: Claude once submitted `summary: "placeholder"` with no causes, which was
  stored as the result.)
* **Fallback.** Any problem (no key, no approval, limit reached, API error, invalid or missing report, too many
  steps) is answered by the deterministic investigator, which follows fixed steps with the same tools. The reason is
  stored and shown. Claude was never called against the real API in this work: all tests use a scripted fake client.
* **States:** `investigating` (runs in the background; the UI polls), `completed`, `failed` (an interrupted run is
  marked failed after 10 minutes). A finished investigation of an open finding shows as **Awaiting review**.

## Human review

* Accept or dismiss needs a **reason** (3 to 500 characters) and is allowed for: the routed owner, anyone who may
  edit the affected project, Sales leadership and Administrators. Finance and read-only roles may investigate and
  view but not decide (unless routed to them). A refusal names who can.
* **Accepting records the decision; it does not change revenue.** Fixing the forecast is a normal edit by someone
  authorised to make it (with its own audit entry). Tested: investigations, runs and accepts leave revenue and revenue
  history untouched.
* Everything is in the finding's audit trail: created, routed, updated, investigation started/finished/failed,
  accepted/dismissed (who, when, reason), auto-resolved, reopened.

## Forecast Health

`GET /v1/qa/health` and the top of the **Quality** tab: open findings (by severity and check), revenue at stake (sum
of open impacts; findings can overlap, so an upper bound), unassigned and awaiting-review counts, owners needing
attention (open count, revenue, age of the oldest), triage rate, and the last run. Status is stated, not scored:
**red** if any open error finding or the latest run failed; **amber** if there are open warnings, or no successful run
in 36 hours (or never); otherwise **green**. The formulas are shown in the UI.

## Authorization

| Action | Who |
| --- | --- |
| Read findings, health, investigations, runs | everyone signed in |
| Run the checks now | Sales leadership, Finance, Administrator |
| Investigate | the above, plus anyone allowed to decide that finding |
| Accept / dismiss | routed owner, project editors, Sales leadership, Administrator |
| Nightly job | the system (no API) |

All enforced in FastAPI; every change needs the CSRF token.

## Data model (migration 0006)

`qa_findings` (reshaped from the unused stub; refuses to migrate if it holds rows), `qa_runs`,
`qa_finding_events` (append-only trigger) and `investigations`. Downgrading removes this milestone's data.

## Not done

* Attestation tracking, the plain-language "ask the agent" Q&A and per-deal phasing overrides (other PRD items).
* Email/Slack alerts: the in-app Quality tab is the notification surface, as in Milestone 3.
* A real Claude call has not been made; behaviour was verified with a scripted client.
