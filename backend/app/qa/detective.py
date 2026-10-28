"""The AI Revenue Detective: explains a QA finding from the records, and never changes anything.

Two investigators produce the same structured result:

* a deterministic one that follows fixed steps with the read-only tools, always available;
* Claude (Anthropic), which uses the same tools in a tool-use loop and finishes by submitting a
  structured report. Claude is used only when an API key is configured AND the enterprise
  data-use approval is switched on (`AI_DATA_USE_APPROVED`) AND the monthly call limit is not
  reached; on any problem the deterministic investigator answers instead and says why.

Whatever the investigator, the result is checked before it is stored: a cause marked "confirmed"
must cite a record that the investigation actually retrieved (otherwise it is downgraded to a
hypothesis), references to records that were never retrieved are dropped, and the financial impact
is always the engine's deterministic figure, never a number from a model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import Investigation, QaFinding, QaFindingEvent, User
from app.qa.tools import TOOLS, ToolContext, call_tool

MAX_ROUNDS = 6
STALE_AFTER = timedelta(minutes=10)
Status = Literal["confirmed", "hypothesis", "insufficient_evidence"]

IMPACT_BASIS = {
    "duplicate_project": "forward revenue that would be counted twice (the smaller row, over open months)",
    "closed_won_missing": "the deal amount that is not yet in the committed forecast",
    "amount_mismatch": "the gap between the HubSpot amount and the committed schedule",
    "stale_row": "committed revenue in the next three months on a row not recently reviewed",
    "mom_anomaly": "the size of the largest month-over-month movement",
    "missing_required": "forward committed revenue on the incomplete row",
    "deal_missing_fields": "the weighted amount that cannot be phased into months",
}


class Cause(BaseModel):
    statement: str = Field(max_length=700)
    status: Status
    evidence_refs: list[str] = Field(default_factory=lambda: [], max_length=12)


class NextStep(BaseModel):
    action: str = Field(max_length=500)
    who: str | None = Field(default=None, max_length=120)


class ModelReport(BaseModel):
    """What an investigator submits. The financial impact is added by the engine, not the model."""

    summary: str = Field(max_length=1200)
    causes: list[Cause] = Field(max_length=10)
    next_steps: list[NextStep] = Field(max_length=8)
    confidence: Literal["high", "medium", "low"]
    limitations: list[str] = Field(default_factory=lambda: [], max_length=6)


class Impact(BaseModel):
    amount: str
    basis: str
    source: Literal["deterministic"] = "deterministic"


class InvestigationResult(ModelReport):
    financial_impact: Impact


def sanitise(report: ModelReport, retrieved: set[str]) -> ModelReport:
    """Enforce the evidence rules on whatever an investigator produced."""
    causes: list[Cause] = []
    for cause in report.causes:
        refs = [r for r in dict.fromkeys(cause.evidence_refs) if r in retrieved]
        status: Status = cause.status
        if status == "confirmed" and not refs:
            status = "hypothesis"  # a cause is only confirmed when it cites a record that was read
        causes.append(Cause(statement=cause.statement, status=status, evidence_refs=refs))
    return report.model_copy(update={"causes": causes})


def finish(report: ModelReport, finding: QaFinding, ctx: ToolContext) -> InvestigationResult:
    own = {f"project:{pid}" for pid in [finding.project_id, *finding.related_project_ids] if pid}
    if finding.opportunity_id:
        own.add(f"deal:{finding.opportunity_id}")  # the finding itself already cites these records
    report = sanitise(report, ctx.refs | own)
    return InvestigationResult(
        **report.model_dump(),
        financial_impact=Impact(
            amount=str(finding.financial_impact),
            basis=IMPACT_BASIS.get(finding.rule_code, "see the finding"),
        ),
    )


# ---- the deterministic investigator ------------------------------------------------------------


def _who(evidence: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = evidence.get("rows") or [{}]
    project: dict[str, Any] = evidence.get("project") or rows[-1]
    owners: list[dict[str, str]] = project.get("owners") or []
    return (
        ", ".join(f"{o['name']} ({o['role'].replace('_', ' ')})" for o in owners)
        or "no owner is assigned"
    )


def _c(statement: str, status: Status, *refs: str | None) -> Cause:
    return Cause(statement=statement, status=status, evidence_refs=[r for r in refs if r])


async def deterministic_report(ctx: ToolContext, finding: QaFinding) -> ModelReport:
    ev = finding.evidence
    pid = finding.project_id
    pref = f"project:{pid}" if pid else None
    causes: list[Cause] = []
    steps: list[NextStep] = []
    limits: list[str] = []
    confidence: Literal["high", "medium", "low"] = "medium"
    rule = finding.rule_code
    who = _who(ev)

    if rule == "mom_anomaly" and pid:
        move = max(ev["movements"], key=lambda m: abs(Decimal(m["change"])))
        month = move["month"]
        causes.append(
            _c(f"Revenue moves from {move['before']} to {move['after']} in {month[:7]} "
               f"({move['change_percent']}%).", "confirmed", pref)
        )  # fmt: skip
        history = await call_tool(ctx, "get_project_history", {"project_id": pid, "limit": 40})
        if "error" in history.data:
            causes.append(
                _c(
                    "The change history cannot be read with your permissions, so the reason and "
                    "author of the change are unknown.",
                    "insufficient_evidence",
                )
            )
            limits.append("Change history was not available to the requester.")
            confidence = "low"
        else:
            edits = [
                e for e in history.data["events"] if e["month"] == month and not e["from_import"]
            ]
            causes.append(_c(f"No reason code is recorded in the change history for {month[:7]}.",
                             "confirmed", pref))  # fmt: skip
            if edits:
                e = edits[0]
                causes.append(
                    _c(f"{month[:7]} was edited in the app by {e['by']} on {e['when'][:10]} "
                       f"({e['from']} to {e['to']}).", "confirmed", e["ref"])
                )  # fmt: skip
                confidence = "high"
            else:
                causes.append(
                    _c(
                        "No in-app edit of this month is recorded, so the figure appears to come "
                        "from the original import or an earlier plan.",
                        "hypothesis",
                        pref,
                    )
                )
        snaps = await call_tool(ctx, "compare_snapshots", {"project_id": pid})
        if snaps.data.get("available"):
            row = next((m for m in snaps.data["months"] if m["month"] == month), None)
            old, new = snaps.data["older"], snaps.data["newer"]
            if row and row["difference"] != "0.00":
                causes.append(_c(f"{month[:7]} was {row['older']} in {old['ref']} and {row['newer']} in "
                                 f"{new['ref']}: it changed between those snapshots.",
                                 "confirmed", old["ref"], new["ref"]))  # fmt: skip
            elif row:
                causes.append(_c(f"{month[:7]} is the same ({row['newer']}) in the two latest snapshots, "
                                 "so it was already in the forecast then.", "confirmed",
                                 old["ref"], new["ref"]))  # fmt: skip
        else:
            limits.append(
                "Fewer than two snapshots exist, so the change cannot be dated from snapshots."
            )
        customer = ev["project"]["customer"]
        deals = await call_tool(ctx, "get_hubspot_deals", {"company": customer})
        for deal in deals.data.get("deals", []):
            if (
                deal["lifecycle"] in ("closed_lost", "closed_won")
                and deal["linked_project"] != pref
            ):
                kind = "Closed Lost" if deal["lifecycle"] == "closed_lost" else "Closed Won"
                causes.append(
                    _c(f"HubSpot shows a {kind} deal for the same customer: '{deal['name']}' "
                       f"({deal['amount']}). Nothing in the records links it to this movement, so "
                       "this is a hypothesis.", "hypothesis", deal["ref"], pref)
                )  # fmt: skip
        owner_note = "the row's owner" if who == "no owner is assigned" else who
        steps = [
            NextStep(action=f"Ask {owner_note} what changed and record a reason code on the month.", who=who),
            NextStep(action="If a related HubSpot deal is listed, check it in HubSpot.", who="Sales Ops"),
            NextStep(action="If the movement is not intended, correct the months (an owner, Sales "
                            "leadership or an Administrator edits it).", who=None),
        ]  # fmt: skip
        summary = (
            f"{ev['project']['row_id']} has an unexplained {move['change_percent']}% movement in "
            f"{month[:7]}; the records confirm the figures but not why they changed."
        )

    elif rule == "duplicate_project" and pid:
        found = await call_tool(ctx, "find_duplicates", {"project_id": pid})
        first, second = ev["rows"]
        causes.append(
            _c(f"{first['row_id']} and {second['row_id']} are for {first['customer']} with "
               f"{'identical' if ev['name_similarity'] >= 1 else 'near-identical'} names; revenue is equal "
               f"in {ev['equal_forward_months']} of {ev['shared_forward_months']} shared forward months.",
               "confirmed", f"project:{first['project_id']}", f"project:{second['project_id']}")
        )  # fmt: skip
        history = await call_tool(ctx, "get_project_history", {"project_id": pid, "limit": 40})
        created = [e for e in history.data.get("events", []) if e["change"] == "create"]
        if created:
            e = created[-1]
            by = "the workbook import" if e["from_import"] else e["by"]
            causes.append(
                _c(
                    f"{second['row_id']} was created by {by} on {e['when'][:10]}.",
                    "confirmed",
                    e["ref"],
                )
            )
        causes.append(
            _c(f"{second['row_id']} was created after {first['row_id']}, so it is probably the copy; only "
               "its owner can say.", "hypothesis", f"project:{second['project_id']}")
        )  # fmt: skip
        steps = [
            NextStep(action=f"Confirm with {who} whether both rows are real work.", who=who),
            NextStep(action="If one is a copy, keep the original and remove the copy's revenue so the "
                            "forward total is not overstated.", who="row owner or Sales leadership"),
        ]  # fmt: skip
        confidence = "high" if found.data.get("candidates") else "medium"
        summary = f"{second['row_id']} duplicates {first['row_id']}; up to {finding.financial_impact} of forward revenue is counted twice."

    elif (
        rule in ("closed_won_missing", "deal_missing_fields", "amount_mismatch")
        and finding.opportunity_id
    ):
        deals = await call_tool(
            ctx, "get_hubspot_deals", {"opportunity_id": finding.opportunity_id}
        )
        deal = deals.data["deals"][0] if deals.data.get("deals") else None
        dref = deal["ref"] if deal else None
        if rule == "closed_won_missing":
            draft = ev.get("draft_project")
            if draft:
                causes.append(_c(f"Draft project {draft['row_id']} exists but its owner has not confirmed it"
                                 + (" and the confirmation is overdue." if ev["overdue"] else "."),
                                 "confirmed", dref, f"project:{draft['project_id']}"))  # fmt: skip
            else:
                causes.append(
                    _c(
                        "No draft project could be created for this deal (HubSpot gave a company ID "
                        "rather than a name, or the project already exists).",
                        "confirmed",
                        dref,
                    )
                )
            steps = [NextStep(action="Confirm the monthly schedule of the draft project so committed revenue "
                                     "replaces the pipeline line.", who=ev["deal"]["owner"])]  # fmt: skip
            confidence = "high"
            summary = f"Closed Won deal {ev['deal']['hubspot_deal_id']} ({ev['deal']['amount']}) is not yet committed."
        elif rule == "deal_missing_fields":
            causes.append(_c(f"The deal has no {', '.join(ev['missing'])}, so it cannot be spread across months.",
                             "confirmed", dref))  # fmt: skip
            steps = [NextStep(action="Complete the missing fields in HubSpot (this app never writes to it).",
                              who=ev["deal"].get("owner") or "Sales Ops")]  # fmt: skip
            confidence = "high"
            summary = f"Deal {ev['deal']['hubspot_deal_id']} cannot be phased."
        else:
            project = ev["project"]
            causes.append(_c(f"HubSpot says {ev['deal']['amount']} but {project['row_id']}'s schedule totals "
                             f"{ev['schedule_total']} ({ev['difference_percent']}% apart).", "confirmed",
                             dref, f"project:{project['project_id']}"))  # fmt: skip
            history = await call_tool(
                ctx, "get_project_history", {"project_id": finding.project_id, "limit": 40}
            )
            closed = deal["close_date"] if deal and deal["close_date"] else None
            later = [e for e in history.data.get("events", []) if not e["from_import"]
                     and closed and e["when"] > closed]  # fmt: skip
            if later:
                e = later[0]
                causes.append(
                    _c(
                        f"The schedule was edited by {e['by']} on {e['when'][:10]}, after the deal "
                        "closed; that may explain the gap.",
                        "hypothesis",
                        e["ref"],
                    )
                )
            else:
                causes.append(
                    _c(
                        "Nothing records when either figure changed, so the reason for the gap is "
                        "not known.",
                        "insufficient_evidence",
                    )
                )
            steps = [NextStep(action="Check which figure is right; update the schedule, or ask Sales Ops to "
                                     "align HubSpot.", who=who)]  # fmt: skip
            summary = f"Deal {ev['deal']['hubspot_deal_id']} and {project['row_id']} disagree by {ev['difference_percent']}%."

    elif rule == "stale_row" and pid:
        history = await call_tool(ctx, "get_project_history", {"project_id": pid, "limit": 10})
        causes.append(_c(f"The row was {ev['basis']} on {ev['last_updated']}, {ev['days_since_update']} days ago.",
                         "confirmed", pref))  # fmt: skip
        recent = [e for e in history.data.get("events", []) if not e["from_import"]]
        causes.append(_c("No in-app edit is recorded, so no one has reviewed the months since.", "confirmed", pref)
                      if not recent else _c(f"The latest in-app edit was on {recent[0]['when'][:10]} by {recent[0]['by']}.", "confirmed", recent[0]["ref"]))  # fmt: skip
        steps = [NextStep(action="Review the next three months and record the update.", who=who)]
        confidence = "high"
        summary = f"{ev['project']['row_id']} has {finding.financial_impact} in the next three months and has not been reviewed for {ev['days_since_update']} days."

    elif rule == "missing_required" and pid:
        owners = await call_tool(ctx, "get_project_owners", {"project_id": pid})
        causes.append(_c("Missing: " + ", ".join(ev["missing"]) + ".", "confirmed", pref))
        if not owners.data.get("has_owner"):
            causes.append(
                _c(
                    "The row has no owner, so it cannot be routed to anyone; only Sales leadership or "
                    "an Administrator can assign one.",
                    "confirmed",
                    pref,
                )
            )
        steps = [NextStep(action="Complete the missing fields.", who=who)]
        confidence = "high"
        summary = f"{ev['project']['row_id']} is incomplete: {', '.join(ev['missing'])}."

    else:
        causes.append(
            _c("This finding type has no automated investigation steps.", "insufficient_evidence")
        )
        summary = finding.summary
        confidence = "low"

    return ModelReport(
        summary=summary, causes=causes, next_steps=steps, confidence=confidence, limitations=limits
    )


# ---- Claude -------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the AI Revenue Detective for BIG's revenue forecasting application.
You investigate one quality-assurance finding about the revenue forecast and explain it.

Rules you must follow:
- You are strictly read-only. You cannot and must not change revenue, accept, dismiss or approve a
  finding, or act for anyone. You only look things up with the provided tools and report.
- Use the tools to gather evidence before concluding. Every record a tool returns has a reference such
  as project:12, revision:34, snapshot:5 or deal:7.
- Classify every claim as "confirmed" (directly shown by a record you retrieved, citing its
  reference), "hypothesis" (plausible but not proven by the records) or "insufficient_evidence".
  Never present a hypothesis as fact and never cite a reference you did not retrieve.
- Do not invent figures. The financial impact is calculated by the system; do not state your own.
- If a tool says you are not authorized, say so as a limitation; do not try to work around it.
- Finish by calling submit_investigation exactly once with your structured report."""

SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_investigation",
    "description": "Submit the final structured investigation report. Call this once, last.",
    "input_schema": ModelReport.model_json_schema(),
}


def make_client(settings: Settings) -> Any:
    import anthropic

    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    return anthropic.AsyncAnthropic(api_key=key, timeout=float(settings.ai_timeout_seconds))


def _blocks(response: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in response.content:
        kind = getattr(block, "type", "")
        if kind == "text":
            out.append({"type": "text", "text": block.text})
        elif kind == "tool_use":
            out.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
            )
    return out


async def claude_report(
    ctx: ToolContext,
    finding: QaFinding,
    settings: Settings,
    client: Any,
    transcript: list[dict[str, Any]],
) -> ModelReport:
    """Run the tool-use loop. Raises on any problem; the caller falls back to the deterministic report."""
    brief = {
        "finding_id": finding.id, "rule": finding.rule_code, "severity": finding.severity,
        "title": finding.title, "summary": finding.summary, "evidence": finding.evidence,
        "suggested_fix": finding.suggested_fix,
        "deterministic_financial_impact": str(finding.financial_impact),
        "requested_by_role_scope": "tools run with the requester's permissions",
    }  # fmt: skip
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Investigate this finding and submit your report.\n\n"
            + json.dumps(brief, default=str),
        }
    ]
    transcript.append({"role": "system", "content": SYSTEM_PROMPT})
    transcript.append({"role": "user", "content": messages[0]["content"]})
    tools = [t.schema() for t in TOOLS.values()] + [SUBMIT_TOOL]
    for _ in range(MAX_ROUNDS):
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        blocks = _blocks(response)
        usage = getattr(response, "usage", None)
        transcript.append(
            {"role": "assistant", "content": blocks,
             "usage": {"input": getattr(usage, "input_tokens", None), "output": getattr(usage, "output_tokens", None)}}
        )  # fmt: skip
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b["type"] == "tool_use"]
        if not uses:
            raise ValueError("The model ended without submitting a report.")
        results: list[dict[str, Any]] = []
        for use in uses:
            if use["name"] == "submit_investigation":
                try:
                    return ModelReport.model_validate(use["input"])
                except ValidationError as exc:
                    raise ValueError(f"The submitted report was invalid: {str(exc)[:300]}") from exc
            outcome = await call_tool(ctx, use["name"], use["input"])
            results.append(
                {"type": "tool_result", "tool_use_id": use["id"],
                 "content": json.dumps({"data": outcome.data, "refs": outcome.refs}, default=str)}
            )  # fmt: skip
        messages.append({"role": "user", "content": results})
        transcript.append(
            {
                "role": "tool",
                "content": [
                    {"id": r["tool_use_id"], "output": r["content"][:6000]} for r in results
                ],
            }
        )
    raise ValueError("The model did not finish within the allowed number of steps.")


# ---- orchestration -----------------------------------------------------------------------------


async def ai_mode(
    session: AsyncSession, settings: Settings, now: datetime
) -> tuple[bool, str | None]:
    """Whether Claude may be used, and if not, why (shown to the user)."""
    if not (settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value()):
        return False, "No Anthropic API key is configured."
    if not settings.ai_data_use_approved:
        return (
            False,
            "Enterprise data-use approval for AI is not switched on (AI_DATA_USE_APPROVED).",
        )
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = await session.scalar(
        select(func.count())
        .select_from(Investigation)
        .where(Investigation.mode == "ai", Investigation.started_at >= month)
    )
    if (used or 0) >= settings.ai_monthly_call_limit:
        return (
            False,
            f"The monthly limit of {settings.ai_monthly_call_limit} AI investigations has been reached.",
        )
    return True, None


async def start_investigation(
    session: AsyncSession,
    finding: QaFinding,
    user: User,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[Investigation, bool]:
    """Create the investigation record (or return the one already running). The caller commits and
    then schedules `run_investigation`. Returns (investigation, created)."""
    now = now or datetime.now(UTC)
    running = await session.scalar(
        select(Investigation)
        .where(Investigation.finding_id == finding.id, Investigation.status == "investigating")
        .order_by(Investigation.id.desc())
    )
    if running is not None and now - running.started_at < STALE_AFTER:
        return running, False
    use_ai, reason = await ai_mode(session, settings, now)
    investigation = Investigation(
        finding_id=finding.id, status="investigating", mode="ai" if use_ai else "deterministic",
        model=settings.anthropic_model if use_ai else None, requested_by_id=user.id,
        requested_by_label=user.name, started_at=now, fallback_reason=reason, steps=[], transcript=[],
    )  # fmt: skip
    session.add(investigation)
    await session.flush()
    session.add(
        QaFindingEvent(
            finding_id=finding.id, event_type="investigation_started", actor_user_id=user.id,
            actor_label=user.name, message=f"Investigation #{investigation.id} started ({investigation.mode}).",
            details={"investigation_id": investigation.id, "mode": investigation.mode},
        )
    )  # fmt: skip
    return investigation, True


async def reap_stale(session: AsyncSession, now: datetime | None = None) -> None:
    """An investigation still 'investigating' long after it started was interrupted (a restart)."""
    now = now or datetime.now(UTC)
    stale = await session.scalars(
        select(Investigation).where(
            Investigation.status == "investigating", Investigation.started_at < now - STALE_AFTER
        )
    )
    for investigation in stale:
        investigation.status = "failed"
        investigation.finished_at = now
        investigation.error_message = (
            "The investigation was interrupted before it finished. Run it again."
        )


async def run_investigation(
    factory: async_sessionmaker[AsyncSession],
    investigation_id: int,
    settings: Settings,
    client_factory: Callable[[Settings], Any] = make_client,
) -> None:
    """Do the investigation in its own session (it runs after the request has returned)."""
    async with factory() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.status != "investigating":
            return
        finding = await session.get(QaFinding, investigation.finding_id)
        user = (
            await session.get(User, investigation.requested_by_id)
            if investigation.requested_by_id
            else None
        )
        try:
            if finding is None or user is None:
                raise LookupError("The finding or the requesting user no longer exists.")
            await session.refresh(user, ["roles"])
            ctx = ToolContext(session, user)
            transcript: list[dict[str, Any]] = []
            report: ModelReport | None = None
            if investigation.mode == "ai":
                try:
                    report = await claude_report(
                        ctx, finding, settings, client_factory(settings), transcript
                    )
                except Exception as exc:  # noqa: BLE001 - any AI problem falls back, with the reason kept
                    investigation.mode = "deterministic"
                    investigation.fallback_reason = f"Claude was unavailable ({type(exc).__name__}: {str(exc)[:300]}); the deterministic investigator answered."
                    ctx = ToolContext(
                        session, user
                    )  # discard whatever the failed attempt retrieved
            if report is None:
                report = await deterministic_report(ctx, finding)
            result = finish(report, finding, ctx)
            investigation.result = result.model_dump()
            investigation.steps = ctx.steps
            investigation.transcript = transcript
            investigation.status = "completed"
            kind, message = (
                "investigated",
                f"Investigation #{investigation.id} completed ({investigation.mode}).",
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never raised into the background task
            await session.rollback()
            investigation = await session.get(Investigation, investigation_id)
            if investigation is None:
                return
            investigation.status = "failed"
            investigation.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            kind, message = "investigation_failed", f"Investigation #{investigation.id} failed."
        investigation.finished_at = datetime.now(UTC)
        session.add(
            QaFindingEvent(
                finding_id=investigation.finding_id, event_type=kind, actor_user_id=investigation.requested_by_id,
                actor_label=investigation.requested_by_label, message=message,
                details={"investigation_id": investigation.id, "mode": investigation.mode},
            )
        )  # fmt: skip
        await session.commit()
