"""QA findings, runs, investigations and the Forecast Health summary.

Everyone signed in can read findings and health (the forecast itself is readable by everyone).
Accepting or dismissing a finding needs a human who is the routed owner, who may edit the affected
project, or Sales leadership / an Administrator, and always needs a recorded reason. Running the
checks on demand and investigating need those people or Finance. Accepting a finding records the
decision only: any change to revenue is a separate, normal edit by someone authorised to make it.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.auth.deps import CurrentUser, require_csrf
from app.auth.permissions import edit_decision, role_names
from app.auth.roles import ADMIN, FINANCE, SALES_LEADERSHIP
from app.config import Settings, get_settings
from app.database import async_session_factory, get_db_session
from app.models import (
    HubspotOpportunity,
    Investigation,
    Project,
    ProjectOwner,
    QaFinding,
    QaFindingEvent,
    QaRun,
    User,
)
from app.qa.detective import (
    ai_mode,
    make_client,
    reap_stale,
    run_investigation,
    start_investigation,
)
from app.qa.engine import QaBusyError, run_qa_safely
from app.qa.rules import RULES
from app.qa_schemas import (
    FindingDetail,
    FindingEventOut,
    FindingOut,
    FindingPage,
    HealthOut,
    InvestigationOut,
    Metric,
    OwnerLoad,
    ProjectRef,
    ReviewRequest,
    RunOut,
)

router = APIRouter(prefix="/qa", tags=["qa"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
ZERO = Decimal("0.00")
REVIEW_ANY = frozenset({SALES_LEADERSHIP, ADMIN})
RUN_ROLES = frozenset({SALES_LEADERSHIP, ADMIN, FINANCE})
CHECKS_OVERDUE_AFTER = timedelta(hours=36)  # the nightly job is expected within this long


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_session_factory


def get_ai_client_factory() -> Callable[[Settings], Any]:
    return make_client


SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
ClientFactory = Annotated[Callable[[Settings], Any], Depends(get_ai_client_factory)]


def _error(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _can_review(user: User, finding: QaFinding, project: Project | None) -> bool:
    if role_names(user) & REVIEW_ANY or finding.routed_to_id == user.id:
        return True
    return project is not None and edit_decision(user, project).allowed


async def _projects(session: AsyncSession, ids: set[int]) -> dict[int, Project]:
    if not ids:
        return {}
    rows = await session.scalars(
        select(Project)
        .where(Project.id.in_(ids))
        .options(
            selectinload(Project.customer),
            selectinload(Project.owners).selectinload(ProjectOwner.user),
        )
    )
    return {p.id: p for p in rows}


async def _latest_investigation_status(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    latest = (
        select(Investigation.finding_id, func.max(Investigation.id).label("latest"))
        .where(Investigation.finding_id.in_(ids))
        .group_by(Investigation.finding_id)
        .subquery()
    )
    rows = await session.execute(
        select(Investigation.finding_id, Investigation.status).join(
            latest, Investigation.id == latest.c.latest
        )
    )
    return {finding_id: status for finding_id, status in rows}


async def _finding_out(
    session: AsyncSession, user: User, findings: list[QaFinding]
) -> list[FindingOut]:
    project_ids = {f.project_id for f in findings if f.project_id}
    projects = await _projects(session, project_ids)
    user_ids = {x for f in findings for x in (f.routed_to_id, f.reviewed_by_id) if x}
    names: dict[int, str] = {}
    if user_ids:
        names = {
            u.id: u.name for u in await session.scalars(select(User).where(User.id.in_(user_ids)))
        }
    statuses = await _latest_investigation_status(session, [f.id for f in findings])
    out: list[FindingOut] = []
    for f in findings:
        latest = statuses.get(f.id)
        out.append(
            FindingOut(
                id=f.id, rule_code=f.rule_code, rule_label=RULES.get(f.rule_code, f.rule_code),
                severity=f.severity, title=f.title, summary=f.summary, status=f.status,
                financial_impact=f.financial_impact, routed_to_id=f.routed_to_id,
                routed_to_name=names.get(f.routed_to_id) if f.routed_to_id else None,
                project_id=f.project_id, opportunity_id=f.opportunity_id,
                related_project_ids=f.related_project_ids, created_at=f.created_at,
                last_seen_at=f.last_seen_at,
                reviewed_by_name=names.get(f.reviewed_by_id) if f.reviewed_by_id else None,
                reviewed_at=f.reviewed_at, review_reason=f.review_reason,
                investigation_status=latest,
                awaiting_review=f.status == "open" and latest == "completed",
                can_review=f.status == "open"
                and _can_review(user, f, projects.get(f.project_id) if f.project_id else None),
            )
        )  # fmt: skip
    return out


# ---- findings --------------------------------------------------------------------------------


@router.get("/findings", response_model=FindingPage)
async def list_findings(
    session: Session,
    user: CurrentUser,
    status: Annotated[
        str | None, Query(description="open, accepted, dismissed, resolved or all")
    ] = "open",
    severity: str | None = None,
    rule: str | None = None,
    mine: Annotated[bool, Query(description="Only findings routed to me")] = False,
    unassigned: bool = False,
    project_id: int | None = None,
    owner_id: int | None = None,
) -> FindingPage:
    stmt = select(QaFinding)
    if status and status != "all":
        stmt = stmt.where(QaFinding.status == status)
    if severity:
        stmt = stmt.where(QaFinding.severity == severity)
    if rule:
        stmt = stmt.where(QaFinding.rule_code == rule)
    if mine:
        stmt = stmt.where(QaFinding.routed_to_id == user.id)
    if unassigned:
        stmt = stmt.where(QaFinding.routed_to_id.is_(None))
    if owner_id:
        stmt = stmt.where(QaFinding.routed_to_id == owner_id)
    if project_id:
        stmt = stmt.where(
            (QaFinding.project_id == project_id)
            | QaFinding.related_project_ids.contains([project_id])
        )
    # Errors first, then the most revenue at stake.
    severity_rank = func.array_position(["error", "warning", "info"], QaFinding.severity)
    rows = list(
        await session.scalars(
            stmt.order_by(severity_rank, QaFinding.financial_impact.desc(), QaFinding.id).limit(500)
        )
    )
    return FindingPage(items=await _finding_out(session, user, rows), total_items=len(rows))


async def _get(session: AsyncSession, finding_id: int, *, lock: bool = False) -> QaFinding:
    stmt = select(QaFinding).where(QaFinding.id == finding_id)
    if lock:
        stmt = stmt.with_for_update()
    finding = await session.scalar(stmt)
    if finding is None:
        raise _error(404, "finding_not_found", "Finding not found.")
    return finding


@router.get("/findings/{finding_id}", response_model=FindingDetail)
async def get_finding(
    finding_id: int, session: Session, user: CurrentUser, settings: AppSettings
) -> FindingDetail:
    finding = await _get(session, finding_id)
    await reap_stale(session)
    await session.commit()
    (base,) = await _finding_out(session, user, [finding])
    ids = {x for x in [finding.project_id, *finding.related_project_ids] if x}
    projects = await _projects(session, ids)
    deal: dict[str, Any] | None = None
    if finding.opportunity_id:
        o = await session.get(HubspotOpportunity, finding.opportunity_id)
        if o:
            deal = {
                "id": o.id, "hubspot_deal_id": o.hubspot_deal_id, "name": o.name, "stage": o.stage,
                "lifecycle": o.lifecycle, "amount": str(o.amount) if o.amount is not None else None,
                "company": o.company_resolved_name,
            }  # fmt: skip
    events = await session.scalars(
        select(QaFindingEvent)
        .where(QaFindingEvent.finding_id == finding.id)
        .order_by(QaFindingEvent.id)
    )
    investigations = await session.scalars(
        select(Investigation)
        .where(Investigation.finding_id == finding.id)
        .order_by(Investigation.id.desc())
        .limit(5)
    )
    available, reason = await ai_mode(session, settings, datetime.now(UTC))
    can_review = base.can_review
    return FindingDetail(
        **base.model_dump(),
        evidence=finding.evidence, suggested_fix=finding.suggested_fix,
        projects=[
            ProjectRef(project_id=p.id, row_id=p.source_row_id, customer=p.customer.name, name=p.name,
                       status=p.status)
            for pid in ids if (p := projects.get(pid))
        ],
        deal=deal,
        events=[FindingEventOut.model_validate(e) for e in events],
        investigations=[InvestigationOut.model_validate(i) for i in investigations],
        can_investigate=finding.status == "open" and (can_review or bool(role_names(user) & RUN_ROLES)),
        ai_available=available, ai_unavailable_reason=reason,
    )  # fmt: skip


async def _decide(
    finding_id: int, body: ReviewRequest, session: AsyncSession, user: User, decision: str
) -> FindingOut:
    reason = body.reason.strip()
    if len(reason) < 3:
        raise _error(
            422, "reason_required", "Give a reason (at least a few words) for your decision."
        )
    finding = await _get(session, finding_id, lock=True)
    project = (
        (await _projects(session, {finding.project_id})).get(finding.project_id)
        if finding.project_id
        else None
    )
    if not _can_review(user, finding, project):
        routed = await session.get(User, finding.routed_to_id) if finding.routed_to_id else None
        owner = routed.name if routed else None
        raise _error(
            403,
            "not_finding_owner",
            "Only the owner this finding is routed to ("
            + (owner or "nobody: it is unassigned")
            + "), someone who can edit the affected project, Sales leadership or an Administrator can decide it.",
            routed_to=owner,
        )
    if finding.status != "open":
        raise _error(409, "not_open", f"This finding is already {finding.status}.")
    finding.status = decision
    finding.reviewed_by_id = user.id
    finding.reviewed_at = datetime.now(UTC)
    finding.review_reason = reason
    session.add(
        QaFindingEvent(
            finding_id=finding.id, event_type=decision, actor_user_id=user.id, actor_label=user.name,
            message=f"{decision.capitalize()} by {user.name}: {reason}",
            details={"reason": reason, "financial_impact": str(finding.financial_impact)},
        )
    )  # fmt: skip
    await session.commit()
    (out,) = await _finding_out(session, user, [finding])
    return out


@router.post(
    "/findings/{finding_id}/accept", response_model=FindingOut, dependencies=[Depends(require_csrf)]
)
async def accept(
    finding_id: int, body: ReviewRequest, session: Session, user: CurrentUser
) -> FindingOut:
    """Accept a finding as real. Records the decision and reason only; fixing the forecast is a
    separate edit by someone authorised to make it."""
    return await _decide(finding_id, body, session, user, "accepted")


@router.post(
    "/findings/{finding_id}/dismiss",
    response_model=FindingOut,
    dependencies=[Depends(require_csrf)],
)
async def dismiss(
    finding_id: int, body: ReviewRequest, session: Session, user: CurrentUser
) -> FindingOut:
    """Dismiss a finding as not a problem, with a reason. It will not be raised again by later runs."""
    return await _decide(finding_id, body, session, user, "dismissed")


# ---- investigations --------------------------------------------------------------------------


@router.post(
    "/findings/{finding_id}/investigate",
    response_model=InvestigationOut,
    status_code=202,
    dependencies=[Depends(require_csrf)],
)
async def investigate(
    finding_id: int,
    background: BackgroundTasks,
    session: Session,
    user: CurrentUser,
    settings: AppSettings,
    factory: SessionFactory,
    client_factory: ClientFactory,
) -> Investigation:
    """Start an investigation. It runs in the background, read-only, as the requesting user."""
    finding = await _get(session, finding_id)
    project = (
        (await _projects(session, {finding.project_id})).get(finding.project_id)
        if finding.project_id
        else None
    )
    if not (_can_review(user, finding, project) or role_names(user) & RUN_ROLES):
        raise _error(
            403,
            "forbidden_role",
            "Investigations are limited to the finding's owner, Sales leadership, Finance and Administrators.",
        )
    if finding.status != "open":
        raise _error(409, "not_open", f"This finding is already {finding.status}.")
    investigation, created = await start_investigation(session, finding, user, settings)
    await session.commit()
    if created:
        background.add_task(run_investigation, factory, investigation.id, settings, client_factory)
    return investigation


@router.get("/investigations/{investigation_id}", response_model=InvestigationOut)
async def get_investigation(
    investigation_id: int, session: Session, _: CurrentUser
) -> Investigation:
    await reap_stale(session)
    await session.commit()
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise _error(404, "investigation_not_found", "Investigation not found.")
    return investigation


# ---- runs ------------------------------------------------------------------------------------


@router.post("/runs", response_model=RunOut, dependencies=[Depends(require_csrf)])
async def run_checks(session: Session, user: CurrentUser, settings: AppSettings) -> QaRun:
    """Run every deterministic check now (Sales leadership, Finance, Administrator)."""
    if not role_names(user) & RUN_ROLES:
        raise _error(
            403,
            "forbidden_role",
            "Running the checks is limited to Sales leadership, Finance and Administrators.",
        )
    try:
        result = await run_qa_safely(session, settings, trigger="manual", actor=user)
    except QaBusyError as exc:
        raise _error(409, "qa_busy", str(exc)) from exc
    run = await session.get(QaRun, result.run_id)
    if run is None or result.status != "completed":
        raise _error(
            502,
            "qa_failed",
            f"The QA run failed: {result.error}. Existing findings are unchanged.",
            run_id=result.run_id,
        )
    return run


@router.get("/runs", response_model=list[RunOut])
async def list_runs(session: Session, _: CurrentUser) -> list[QaRun]:
    return list(await session.scalars(select(QaRun).order_by(QaRun.id.desc()).limit(20)))


# ---- Forecast Health -------------------------------------------------------------------------


@router.get("/health", response_model=HealthOut)
async def health(session: Session, user: CurrentUser, settings: AppSettings) -> HealthOut:
    """A transparent, deterministic summary: counts and sums over the open findings. There is no
    hidden score; the status follows the stated rules."""
    now = datetime.now(UTC)
    open_rows = list(await session.scalars(select(QaFinding).where(QaFinding.status == "open")))
    by_severity: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for f in open_rows:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_rule[f.rule_code] = by_rule.get(f.rule_code, 0) + 1
    at_stake = sum((f.financial_impact for f in open_rows), ZERO)
    statuses = await _latest_investigation_status(session, [f.id for f in open_rows])
    awaiting = sum(1 for f in open_rows if statuses.get(f.id) == "completed")

    names = {u.id: u.name for u in await session.scalars(select(User))}
    loads: dict[int | None, list[QaFinding]] = {}
    for f in open_rows:
        loads.setdefault(f.routed_to_id, []).append(f)
    owners = sorted(
        (
            OwnerLoad(
                user_id=uid, name=names.get(uid, "Unassigned") if uid else "Unassigned (needs an owner)",
                open_findings=len(items), revenue_at_stake=sum((i.financial_impact for i in items), ZERO),
                oldest_days=max((now - i.created_at).days for i in items),
            )
            for uid, items in loads.items()
        ),
        key=lambda o: (-o.open_findings, -o.revenue_at_stake),
    )[:10]  # fmt: skip

    decided = (
        await session.scalar(
            select(func.count())
            .select_from(QaFinding)
            .where(QaFinding.status.in_(("accepted", "dismissed")))
        )
        or 0
    )
    total_ever = decided + len(open_rows)
    last_run = await session.scalar(select(QaRun).order_by(QaRun.id.desc()).limit(1))
    last_success = await session.scalar(
        select(QaRun).where(QaRun.status == "completed").order_by(QaRun.id.desc()).limit(1)
    )
    overdue = last_success is None or (
        last_success.finished_at is not None
        and now - last_success.finished_at > CHECKS_OVERDUE_AFTER
    )

    reasons: list[str] = []
    level = "green"
    errors = by_severity.get("error", 0)
    if errors:
        level = "red"
        reasons.append(f"{errors} open error finding(s).")
    if last_run is not None and last_run.status == "failed":
        level = "red"
        reasons.append("The latest QA run failed.")
    if overdue:
        if level == "green":
            level = "amber"
        reasons.append(
            "No successful QA run in the last 36 hours."
            if last_success
            else "The checks have never run."
        )
    if by_severity.get("warning", 0) and level == "green":
        level = "amber"
    if by_severity.get("warning", 0):
        reasons.append(f"{by_severity['warning']} open warning(s).")
    if not reasons:
        reasons.append("No open findings and the checks are current.")

    metrics = [
        Metric(key="open", label="Open findings", value=str(len(open_rows)),
               formula="count of findings with status open"),
        Metric(key="revenue_at_stake", label="Revenue at stake", value=str(at_stake),
               formula="sum of each open finding's deterministic financial impact; findings can overlap, so treat it as an upper bound"),
        Metric(key="unassigned", label="Unassigned findings", value=str(len(loads.get(None, []))),
               formula="open findings with no owner to route to"),
        Metric(key="triage_rate", label="Triaged", value=f"{(decided * 100 // total_ever) if total_ever else 100}%",
               formula="(accepted + dismissed) / (accepted + dismissed + open)"),
        Metric(key="stale_rows", label="Rows overdue for review", value=str(by_rule.get("stale_row", 0)),
               formula=f"open stale_row findings (rows in the 3-month window not updated for more than {settings.qa_stale_days} days)"),
    ]  # fmt: skip
    return HealthOut(
        status=level, status_reasons=reasons, open_total=len(open_rows), open_by_severity=by_severity,
        open_by_rule=by_rule, revenue_at_stake=at_stake, unassigned_open=len(loads.get(None, [])),
        awaiting_review=awaiting, owners=owners, metrics=metrics,
        last_run=RunOut.model_validate(last_run) if last_run else None,
        last_success=RunOut.model_validate(last_success) if last_success else None,
        checks_overdue=overdue, as_of=date.today(), can_run=bool(role_names(user) & RUN_ROLES),
    )  # fmt: skip
