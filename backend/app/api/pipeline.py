"""HubSpot pipeline endpoints: weighted deals, combined forecast, sync and draft confirmation.

Reading is open to every signed-in user (like the committed forecast). Triggering a sync needs
Sales leadership or an Administrator; changing stage probabilities needs an Administrator;
confirming a draft needs the edit rights for that project. Everything is enforced here.
"""

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.queries import ProjectFilters, apply_filters
from app.auth.deps import CurrentUser, require_csrf
from app.auth.permissions import edit_decision, owner_summary, role_names
from app.auth.roles import ADMIN, SALES_LEADERSHIP
from app.config import Settings, get_settings
from app.database import get_db_session
from app.fiscal import fiscal_period, fiscal_year
from app.locks import SYNC_LOCK_KEY
from app.models import (
    Category,
    Customer,
    DraftConfirmation,
    HubspotOpportunity,
    HubspotStage,
    HubspotSyncFinding,
    HubspotSyncRun,
    PipelineEvent,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    User,
)
from app.pipeline.drafts import (
    DRAFT_STATUS,
    DraftError,
    confirm_draft,
    load_project_for_confirm,
    schedule_variance,
)
from app.pipeline.forecast import (
    COUNTING,
    DealFilters,
    DealView,
    Window,
    aggregate,
    forecast_window,
    load_views,
    matches,
)
from app.pipeline.source import build_source
from app.pipeline.sync import run_sync
from app.pipeline_schemas import (
    ConfirmationItem,
    ConfirmRequest,
    ConfirmResult,
    DealDetail,
    DealPage,
    DealSummary,
    DraftInfo,
    EventOut,
    FilterOptions,
    FindingOut,
    ForecastMonth,
    ForecastTotals,
    ForecastYear,
    LinkedProject,
    MonthAmount,
    Person,
    ScheduleCheck,
    StageOut,
    StageUpdate,
    SyncRunOut,
    SyncStatus,
    Unphased,
)

router = APIRouter(tags=["pipeline"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
ZERO = Decimal("0.00")
SyncRoles = frozenset({ADMIN, SALES_LEADERSHIP})


def _error(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def forecast_filters(
    owner: Annotated[str | None, Query(description="Owner user code")] = None,
    practice: Annotated[str | None, Query(description="TECH / practice")] = None,
    region: str | None = None,
    industry: str | None = None,
    category: str | None = None,
    customer: str | None = None,
    q: str | None = None,
) -> DealFilters:
    return DealFilters(owner, practice, region, industry, category, customer, q)


Filters = Annotated[DealFilters, Depends(forecast_filters)]


def _committed_filters(f: DealFilters) -> ProjectFilters:
    return ProjectFilters(
        q=f.q, practice=f.practice, region=f.region, industry=f.industry, category=f.category,
        customer=f.customer, owner=f.owner,
    )  # fmt: skip


# ---- views over deals -----------------------------------------------------------------------


async def _finding_counts(session: AsyncSession) -> dict[int, int]:
    run_id = await session.scalar(
        select(HubspotSyncRun.id)
        .where(HubspotSyncRun.status == "completed")
        .order_by(HubspotSyncRun.id.desc())
        .limit(1)
    )
    if run_id is None:
        return {}
    rows = await session.execute(
        select(HubspotSyncFinding.opportunity_id, func.count())
        .where(HubspotSyncFinding.run_id == run_id, HubspotSyncFinding.opportunity_id.is_not(None))
        .group_by(HubspotSyncFinding.opportunity_id)
    )
    return {int(opp): n for opp, n in rows if opp is not None}


def _draft_info(view: DealView, now: datetime) -> DraftInfo | None:
    c = view.confirmation
    if c is None or view.linked_project is None:
        return None
    owner = (
        next((o.user.name for o in view.linked_project.owners), None)
        if view.linked_project.owners
        else None
    )
    return DraftInfo(
        project_id=c.project_id,
        owner_name=owner,
        requested_at=c.requested_at,
        due_at=c.due_at,
        overdue=c.confirmed_at is None and now > c.due_at,
        confirmed_at=c.confirmed_at,
        possible_duplicates=c.possible_duplicates or [],
    )


def _summary(view: DealView, now: datetime) -> DealSummary:
    deal = view.deal
    phasing = view.phasing
    project = view.linked_project
    return DealSummary(
        id=deal.id,
        hubspot_deal_id=deal.hubspot_deal_id,
        name=deal.name,
        company=view.company,
        company_id=deal.company_hubspot_id,
        owner_name=deal.owner_name,
        owner_code=view.owner.code if view.owner else None,
        stage=deal.stage,
        probability=view.probability,
        amount=deal.amount,
        weighted_amount=view.weighted_amount,
        expected_start=deal.expected_start,
        duration_months=deal.duration_months,
        close_date=deal.close_date,
        practice=deal.practice,
        region=deal.region,
        industry=view.industry,
        lifecycle=deal.lifecycle,
        status=view.status,
        counts_in_forecast=view.counts,
        phaseable=phasing.phaseable if phasing else True,
        unphased_reason=phasing.reason if phasing and not phasing.phaseable else None,
        linked_project=(
            LinkedProject(
                id=project.id,
                source_row_id=project.source_row_id,
                name=project.name,
                status=project.status,
            )
            if project
            else None
        ),  # fmt: skip
        draft=_draft_info(view, now),
        findings=view.findings,
    )


async def _views(session: AsyncSession, window: Window, f: DealFilters) -> list[DealView]:
    counts = await _finding_counts(session)
    views = await load_views(session, window)
    for view in views:
        view.findings = counts.get(view.deal.id, 0)
    return [v for v in views if matches(v, f)]


# ---- deals -------------------------------------------------------------------------------


def _sort_key(view: DealView, sort: str) -> tuple[Any, ...]:
    name = view.deal.name
    if sort == "amount":
        return (-(view.deal.amount or ZERO), name)
    if sort == "name":
        return (name.lower(),)
    if sort == "start":
        return (view.deal.expected_start or date.max, name)
    return (-view.weighted_amount, name)


@router.get("/pipeline/deals", response_model=DealPage)
async def list_deals(
    session: Session,
    _: CurrentUser,
    filters: Filters,
    status: Annotated[
        str | None,
        Query(
            description=(
                "weighted, awaiting_confirmation, closed_won_pending, linked, closed_lost, "
                "excluded, or counting (everything that contributes)"
            )
        ),
    ] = None,
    sort: Literal["weighted", "amount", "name", "start"] = "weighted",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DealPage:
    window = await forecast_window(session)
    views = await _views(session, window, filters)
    if status == "counting":
        views = [v for v in views if v.status in COUNTING]
    elif status:
        views = [v for v in views if v.status == status]
    views.sort(key=lambda v: _sort_key(v, sort))
    now = datetime.now(UTC)
    start = (page - 1) * page_size
    return DealPage(
        items=[_summary(v, now) for v in views[start : start + page_size]],
        page=page,
        page_size=page_size,
        total_items=len(views),
        total_pages=ceil(len(views) / page_size),
        weighted_total=sum((v.weighted_amount for v in views), ZERO),
    )


@router.get("/pipeline/deals/{deal_id}", response_model=DealDetail)
async def get_deal(deal_id: int, session: Session, user: CurrentUser) -> DealDetail:
    window = await forecast_window(session)
    view = next(
        (v for v in await _views(session, window, DealFilters()) if v.deal.id == deal_id), None
    )
    if view is None:
        raise _error(404, "deal_not_found", "Deal not found.")
    now = datetime.now(UTC)
    summary = _summary(view, now)

    run_id = await session.scalar(
        select(HubspotSyncRun.id)
        .where(HubspotSyncRun.status == "completed")
        .order_by(HubspotSyncRun.id.desc())
        .limit(1)
    )
    findings = (
        list(
            await session.scalars(
                select(HubspotSyncFinding)
                .where(
                    HubspotSyncFinding.run_id == run_id,
                    HubspotSyncFinding.opportunity_id == deal_id,
                )
                .order_by(HubspotSyncFinding.id)
            )
        )
        if run_id
        else []
    )
    events = list(
        await session.scalars(
            select(PipelineEvent)
            .where(PipelineEvent.opportunity_id == deal_id)
            .order_by(PipelineEvent.created_at.desc(), PipelineEvent.id.desc())
            .limit(50)
        )
    )

    check: ScheduleCheck | None = None
    can_confirm = False
    if view.confirmation is not None and view.linked_project is not None:
        schedule_total = await session.scalar(
            select(func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)).where(
                ProjectMonthlyRevenue.project_id == view.linked_project.id
            )
        )
        outside = view.phasing.outside_window if view.phasing else ZERO
        check = ScheduleCheck(
            **schedule_variance(view.deal.amount, Decimal(schedule_total or 0), outside)
        )
        can_confirm = (
            view.confirmation.confirmed_at is None
            and view.linked_project.status == DRAFT_STATUS
            and edit_decision(
                user, await _project_for_decision(session, view.linked_project.id)
            ).allowed
        )

    phasing = view.phasing
    return DealDetail(
        **summary.model_dump(),
        monthly=[MonthAmount(month=m, amount=a) for m, a in sorted(phasing.monthly.items())]
        if phasing
        else [],
        outside_window=phasing.outside_window if phasing else ZERO,
        findings_list=[FindingOut.model_validate(f) for f in findings],
        events=[EventOut.model_validate(e) for e in events],
        schedule_check=check,
        can_confirm=can_confirm,
        raw=view.deal.raw,
    )


async def _project_for_decision(session: AsyncSession, project_id: int) -> Project:
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
    )
    if project is None:  # pragma: no cover - the FK guarantees it
        raise _error(404, "project_not_found", "Project not found.")
    return project


# ---- combined forecast totals ------------------------------------------------------------


@router.get("/forecast/monthly", response_model=ForecastTotals)
async def forecast_monthly(session: Session, _: CurrentUser, filters: Filters) -> ForecastTotals:
    """Committed, weighted pipeline and combined totals by month and fiscal year.

    Committed excludes unconfirmed drafts; weighted excludes deals represented by a committed
    project, so a deal is never in both. The same filters apply to both halves.
    """
    window = await forecast_window(session)
    stmt = (
        select(
            ProjectMonthlyRevenue.month, func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)
        )
        .join(Project, Project.id == ProjectMonthlyRevenue.project_id)
        .join(Customer, Customer.id == Project.customer_id)
        .where(Project.status != DRAFT_STATUS)
        .group_by(ProjectMonthlyRevenue.month)
    )
    committed = {
        month: Decimal(total)
        for month, total in await session.execute(apply_filters(stmt, _committed_filters(filters)))
    }
    agg = aggregate(await _views(session, window, filters))

    months = sorted(set(window.months) | set(committed) | set(agg.weighted_by_month))
    rows = [
        ForecastMonth(
            month=m, fiscal_year=fiscal_year(m), fiscal_period=fiscal_period(m),
            committed=committed.get(m, ZERO), weighted=agg.weighted_by_month.get(m, ZERO),
            total=committed.get(m, ZERO) + agg.weighted_by_month.get(m, ZERO),
        )
        for m in months
    ]  # fmt: skip
    by_year: dict[str, list[ForecastMonth]] = defaultdict(list)
    for row in rows:
        by_year[row.fiscal_year].append(row)
    committed_total = sum((r.committed for r in rows), ZERO)
    weighted_total = sum((r.weighted for r in rows), ZERO)
    return ForecastTotals(
        months=rows,
        fiscal_years=[
            ForecastYear(
                fiscal_year=year,
                committed=sum((r.committed for r in group), ZERO),
                weighted=sum((r.weighted for r in group), ZERO),
                total=sum((r.total for r in group), ZERO),
            )
            for year, group in sorted(by_year.items())
        ],
        committed_total=committed_total,
        weighted_total=weighted_total,
        combined_total=committed_total + weighted_total,
        weighted_deals=agg.deal_count,
        unphased=Unphased(deals=agg.unphased_deals, weighted_amount=agg.unphased_weighted),
        outside_window_weighted=agg.outside_window_weighted,
        filters_applied=filters.active(),
    )


@router.get("/pipeline/filter-options", response_model=FilterOptions)
async def filter_options(session: Session, _: CurrentUser) -> FilterOptions:
    """Values for the forecast filters, from committed projects and pipeline deals together."""

    async def column(stmt: Any) -> list[str]:
        return sorted({v for (v,) in await session.execute(stmt) if v})

    owners = await session.execute(
        select(User.code, User.name)
        .where(User.code.is_not(None))
        .where(
            User.id.in_(select(ProjectOwner.user_id))
            | User.id.in_(select(HubspotOpportunity.owner_user_id))
        )
        .order_by(User.name)
    )
    return FilterOptions(
        practices=sorted(
            set(await column(select(Project.practice)))
            | set(await column(select(HubspotOpportunity.practice)))
        ),
        regions=sorted(
            set(await column(select(Project.region)))
            | set(await column(select(HubspotOpportunity.region)))
        ),
        industries=await column(select(Project.industry)),
        categories=await column(select(Category.code)),
        customers=sorted(
            set(await column(select(Customer.name)))
            | set(await column(select(HubspotOpportunity.company_resolved_name)))
        ),
        owners=[Person(code=c, name=n) for c, n in owners if c],
    )


# ---- stages -------------------------------------------------------------------------------


@router.get("/pipeline/stages", response_model=list[StageOut])
async def list_stages(session: Session, _: CurrentUser) -> list[HubspotStage]:
    return list(await session.scalars(select(HubspotStage).order_by(HubspotStage.position)))


@router.put(
    "/pipeline/stages/{stage_id}", response_model=StageOut, dependencies=[Depends(require_csrf)]
)
async def update_stage(
    stage_id: int, body: StageUpdate, user: CurrentUser, session: Session
) -> HubspotStage:
    """Administrators maintain stage probabilities and the forecast-stage rule (PRD section 6)."""
    if ADMIN not in role_names(user):
        raise _error(403, "admin_only", "Only an Administrator can change stage probabilities.")
    stage = await session.scalar(
        select(HubspotStage).where(HubspotStage.id == stage_id).with_for_update()
    )
    if stage is None:
        raise _error(404, "stage_not_found", "Stage not found.")

    changes: list[str] = []
    if body.probability is not None:
        if isinstance(body.probability, bool) or not isinstance(
            body.probability, int | float | str
        ):
            raise _error(
                422, "invalid_probability", "Probability must be a number between 0 and 1."
            )
        try:
            value = Decimal(
                repr(body.probability)
                if isinstance(body.probability, float)
                else str(body.probability)
            )
        except InvalidOperation:
            raise _error(
                422, "invalid_probability", "Probability must be a number between 0 and 1."
            ) from None
        if (
            not value.is_finite()
            or not Decimal(0) <= value <= Decimal(1)
            or value != value.quantize(Decimal("0.0001"))
        ):
            raise _error(
                422,
                "invalid_probability",
                "Probability must be between 0 and 1 with at most 4 decimals.",
            )
        value = value.quantize(Decimal("0.0001"))
        if value != stage.probability:
            changes.append(f"probability {stage.probability} -> {value}")
            stage.probability = value
    if body.in_forecast is not None:
        if not isinstance(body.in_forecast, bool):
            raise _error(422, "invalid_in_forecast", "in_forecast must be true or false.")
        if stage.kind != "open":
            raise _error(
                422, "stage_not_open", "Only open stages can be moved in or out of the forecast."
            )
        if body.in_forecast != stage.in_forecast:
            changes.append(f"in_forecast {stage.in_forecast} -> {body.in_forecast}")
            stage.in_forecast = body.in_forecast
    if changes:
        stage.updated_at = datetime.now(UTC)
        session.add(
            PipelineEvent(
                event_type="stage_updated", actor_user_id=user.id, actor_label=user.name,
                message=f"Stage '{stage.name}': " + "; ".join(changes),
            )
        )  # fmt: skip
        await session.commit()
    return stage


# ---- sync ------------------------------------------------------------------------------------


@router.get("/pipeline/sync/status", response_model=SyncStatus)
async def sync_status(session: Session, user: CurrentUser, settings: AppSettings) -> SyncStatus:
    last = await session.scalar(select(HubspotSyncRun).order_by(HubspotSyncRun.id.desc()).limit(1))
    success = await session.scalar(
        select(HubspotSyncRun)
        .where(HubspotSyncRun.status == "completed")
        .order_by(HubspotSyncRun.id.desc())
        .limit(1)
    )
    stale, reason = False, None
    now = datetime.now(UTC)
    if success is None:
        stale, reason = True, "never"
    elif last is not None and last.status == "failed":
        stale, reason = True, "failed"
    elif (
        success.finished_at
        and (now - success.finished_at).total_seconds() > settings.hubspot_stale_after_minutes * 60
    ):
        stale, reason = True, "old"
    return SyncStatus(
        last_run=SyncRunOut.model_validate(last) if last else None,
        last_success=SyncRunOut.model_validate(success) if success else None,
        stale=stale,
        reason=reason,
        stale_since=success.finished_at if success else None,
        stale_after_minutes=settings.hubspot_stale_after_minutes,
        can_sync=bool(role_names(user) & SyncRoles),
        configured_source=settings.hubspot_source,
    )


@router.post("/pipeline/sync", response_model=SyncRunOut, dependencies=[Depends(require_csrf)])
async def trigger_sync(
    session: Session, user: CurrentUser, settings: AppSettings
) -> HubspotSyncRun:
    """Run a sync now. A failed sync answers 502 and leaves the last good data untouched."""
    if not role_names(user) & SyncRoles:
        raise _error(
            403, "sync_not_permitted", "Only Sales leadership or an Administrator can run a sync."
        )
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SYNC_LOCK_KEY}
    )
    if not locked:
        raise _error(409, "sync_in_progress", "A sync is already running.")
    token = (
        settings.hubspot_access_token.get_secret_value() if settings.hubspot_access_token else None
    )
    source = build_source(settings.hubspot_source, settings.hubspot_workbook_path, token)
    report = await run_sync(session, source, settings, actor=user)
    if report.status != "completed":
        raise _error(
            502, "sync_failed",
            f"The HubSpot sync failed: {report.error}. The forecast is unchanged.",
            error=report.error,
        )  # fmt: skip
    run = await session.get(HubspotSyncRun, report.run_id)
    assert run is not None
    return run


# ---- draft confirmation --------------------------------------------------------------------


@router.get("/pipeline/confirmations", response_model=list[ConfirmationItem])
async def list_confirmations(
    session: Session, user: CurrentUser, scope: Literal["mine", "all"] = "mine"
) -> list[ConfirmationItem]:
    """Drafts awaiting confirmation. 'mine' lists only those the caller may confirm."""
    rows = await session.execute(
        select(DraftConfirmation, HubspotOpportunity)
        .join(HubspotOpportunity, HubspotOpportunity.id == DraftConfirmation.opportunity_id)
        .where(DraftConfirmation.confirmed_at.is_(None))
        .order_by(DraftConfirmation.due_at)
    )
    now = datetime.now(UTC)
    items: list[ConfirmationItem] = []
    for confirmation, deal in rows:
        project = await _project_for_decision(session, confirmation.project_id)
        allowed = edit_decision(user, project).allowed
        if scope == "mine" and not allowed:
            continue
        total = await session.scalar(
            select(func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)).where(
                ProjectMonthlyRevenue.project_id == project.id
            )
        )
        items.append(
            ConfirmationItem(
                project_id=project.id, project_name=project.name, customer=project.customer.name,
                deal_id=deal.id, hubspot_deal_id=deal.hubspot_deal_id,
                owner_name=project.owners[0].user.name if project.owners else None,
                amount=deal.amount, schedule_total=Decimal(total or 0),
                requested_at=confirmation.requested_at, due_at=confirmation.due_at,
                overdue=now > confirmation.due_at,
                has_possible_duplicates=bool(confirmation.possible_duplicates),
                can_confirm=allowed,
            )
        )  # fmt: skip
    return items


@router.post(
    "/pipeline/drafts/{project_id}/confirm",
    response_model=ConfirmResult,
    dependencies=[Depends(require_csrf)],
)
async def confirm(
    project_id: int, body: ConfirmRequest, user: CurrentUser, session: Session
) -> ConfirmResult:
    """The owner confirms the monthly schedule: the draft becomes committed and the weighted line
    is retired in the same transaction, so the deal is counted exactly once."""
    project = await load_project_for_confirm(session, project_id)
    if project is None:
        raise _error(404, "project_not_found", "Project not found.")
    decision = edit_decision(user, project)
    if not decision.allowed:
        raise _error(
            403,
            decision.code or "forbidden",
            decision.message or "You cannot confirm this project.",
            owners=owner_summary(project),
        )  # fmt: skip
    try:
        confirmation = await confirm_draft(
            session, project, user, category=body.category, industry=body.industry,
            acknowledge_duplicates=body.acknowledge_possible_duplicate, now=datetime.now(UTC),
        )  # fmt: skip
    except DraftError as exc:
        await session.rollback()
        raise _error(exc.status, exc.code, exc.message) from exc

    deal = await session.get(HubspotOpportunity, confirmation.opportunity_id)
    total = await session.scalar(
        select(func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)).where(
            ProjectMonthlyRevenue.project_id == project_id
        )
    )
    window = await forecast_window(session)
    outside = ZERO
    if deal is not None:
        views = [v for v in await load_views(session, window) if v.deal.id == deal.id]
        outside = views[0].phasing.outside_window if views and views[0].phasing else ZERO
    check = ScheduleCheck(
        **schedule_variance(deal.amount if deal else None, Decimal(total or 0), outside)
    )
    assert confirmation.confirmed_at is not None
    await session.commit()
    return ConfirmResult(
        project_id=project_id, status=project.status, confirmed_at=confirmation.confirmed_at,
        weighted_line_retired=True, schedule_check=check,
    )  # fmt: skip
