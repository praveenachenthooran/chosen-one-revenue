from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.queries import (
    Filters,
    apply_filters,
    attention_count,
    latest_run_id,
    month_columns,
    total_revenue,
)
from app.auth.deps import CurrentUser
from app.auth.permissions import can_view_history, edit_decision
from app.database import get_db_session
from app.fiscal import fiscal_period, fiscal_year
from app.models import (
    Customer,
    ImportIssue,
    ImportRun,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
)
from app.schemas import (
    AttentionOut,
    FiscalYearTotal,
    MonthColumn,
    MonthlyAmount,
    MonthlyTotal,
    MonthsResponse,
    OwnerOut,
    ProjectDetail,
    ProjectPage,
    ProjectRevenue,
    ProjectSummary,
    TotalsResponse,
)

router = APIRouter(tags=["forecast"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
SortKey = Literal["attention", "customer", "project", "total", "updated"]


def _owner(project: Project, role: str) -> OwnerOut | None:
    for owner in project.owners:
        if owner.role == role:
            return OwnerOut(role=owner.role, code=owner.user.code, name=owner.user.name)
    return None


def _summary(
    project: Project,
    total: Decimal,
    attention: int,
    monthly: dict[date, Decimal] | None,
    can_edit: bool,
) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        source_row_id=project.source_row_id,
        customer=project.customer.name,
        name=project.name,
        status=project.status,
        practice=project.practice,
        region=project.region,
        industry=project.industry,
        country=project.country,
        billing_type=project.billing_type,
        category=project.category_code,
        client_partner=_owner(project, "client_partner"),
        ae=_owner(project, "ae"),
        commercial_owner=_owner(project, "commercial_owner"),
        total_revenue=total,
        attention_count=attention,
        last_updated=project.source_last_updated,
        can_edit=can_edit,
        monthly=monthly,
    )


@router.get("/months", response_model=MonthsResponse)
async def list_months(session: Session) -> MonthsResponse:
    """Fiscal month columns for the forecast, in order, with closed/forecast flags."""
    as_of = await session.scalar(
        select(ImportRun.source_as_of)
        .where(ImportRun.status == "completed")
        .order_by(ImportRun.id.desc())
        .limit(1)
    )
    months = await month_columns(session)
    first_open = as_of.replace(day=1) if as_of else None
    return MonthsResponse(
        as_of=as_of,
        months=[
            MonthColumn(
                month=m,
                label=f"{m:%b %Y}",
                fiscal_year=fiscal_year(m),
                fiscal_period=fiscal_period(m),
                is_closed=first_open is not None and m < first_open,
            )
            for m in months
        ],
    )


@router.get("/projects", response_model=ProjectPage)
async def list_projects(
    session: Session,
    user: CurrentUser,
    filters: Filters,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    sort: SortKey = "attention",
    descending: bool = False,
    include_revenue: Annotated[
        bool, Query(description="Include the monthly amounts for each project")
    ] = False,
) -> ProjectPage:
    """Projects, filtered and paginated. By default, projects needing attention come first."""
    base = select(Project.id).join(Customer, Customer.id == Project.customer_id)
    base = apply_filters(base, filters)
    total_items = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    order_columns = {
        "attention": [attention_count.desc(), Customer.name, Project.name],
        "customer": [Customer.name, Project.name],
        "project": [Project.name, Customer.name],
        "total": [total_revenue, Customer.name, Project.name],
        "updated": [Project.source_last_updated, Customer.name, Project.name],
    }[sort]
    if descending:
        order_columns = [c.desc() if sort != "attention" and i == 0 else c
                         for i, c in enumerate(order_columns)]  # fmt: skip
    stmt = (
        select(Project, total_revenue.label("total"), attention_count.label("attention"))
        .join(Customer, Customer.id == Project.customer_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
    )
    stmt = (
        apply_filters(stmt, filters)
        .order_by(*order_columns, Project.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await session.execute(stmt)).all()

    monthly: dict[int, dict[date, Decimal]] = defaultdict(dict)
    if include_revenue and rows:
        revenue = await session.execute(
            select(
                ProjectMonthlyRevenue.project_id,
                ProjectMonthlyRevenue.month,
                ProjectMonthlyRevenue.amount,
            ).where(ProjectMonthlyRevenue.project_id.in_([p.id for p, _, _ in rows]))
        )
        for project_id, month, amount in revenue:
            monthly[project_id][month] = amount

    return ProjectPage(
        items=[
            _summary(
                p,
                Decimal(total),
                int(attention),
                monthly[p.id] if include_revenue else None,
                edit_decision(user, p).allowed,
            )
            for p, total, attention in rows
        ],
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=ceil(total_items / page_size),
    )


async def _load_project(session: AsyncSession, project_id: int) -> Project:
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _project_months(session: AsyncSession, project_id: int) -> list[MonthlyAmount]:
    rows = await session.scalars(
        select(ProjectMonthlyRevenue)
        .where(ProjectMonthlyRevenue.project_id == project_id)
        .order_by(ProjectMonthlyRevenue.month)
    )
    return [
        MonthlyAmount(
            month=r.month,
            fiscal_year=fiscal_year(r.month),
            amount=r.amount,
            baseline_amount=r.baseline_amount,
            edited=r.baseline_amount != r.amount,
            source_cell=r.source_cell,
        )
        for r in rows
    ]


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: int, session: Session, user: CurrentUser) -> ProjectDetail:
    project = await _load_project(session, project_id)
    months = await _project_months(session, project_id)
    issues = await session.scalars(
        select(ImportIssue)
        .where(
            ImportIssue.run_id == latest_run_id,
            ImportIssue.source_row_id == project.source_row_id,
            ImportIssue.severity == "warning",
        )
        .order_by(ImportIssue.id)
    )
    attention = [AttentionOut(code=i.code, message=i.message, field=i.field) for i in issues]
    total = sum((m.amount for m in months), Decimal("0.00"))
    summary = _summary(
        project,
        total,
        len(attention),
        {m.month: m.amount for m in months},
        edit_decision(user, project).allowed,
    )
    return ProjectDetail(
        **summary.model_dump(exclude={"monthly"}),
        monthly=summary.monthly,
        source_row_number=project.source_row_number,
        last_updated_by=project.source_last_updated_by,
        owners=[OwnerOut(role=o.role, code=o.user.code, name=o.user.name) for o in project.owners],
        monthly_revenue=months,
        attention=attention,
        can_view_history=can_view_history(user, project),
    )


@router.get("/projects/{project_id}/revenue", response_model=ProjectRevenue)
async def get_project_revenue(project_id: int, session: Session) -> ProjectRevenue:
    """Monthly revenue values for one project. Blank source months are simply absent."""
    await _load_project(session, project_id)
    months = await _project_months(session, project_id)
    return ProjectRevenue(
        project_id=project_id,
        months=months,
        total=sum((m.amount for m in months), Decimal("0.00")),
    )


@router.get("/revenue/monthly-totals", response_model=TotalsResponse)
async def monthly_totals(
    session: Session,
    filters: Filters,
    basis: Annotated[
        Literal["current", "baseline"],
        Query(description="current = latest forecast; baseline = as loaded by the Excel import"),
    ] = "current",
) -> TotalsResponse:
    """Total revenue per fiscal month and per fiscal year for the filtered projects."""
    column = (
        ProjectMonthlyRevenue.amount
        if basis == "current"
        else ProjectMonthlyRevenue.baseline_amount
    )
    stmt = (
        select(
            ProjectMonthlyRevenue.month,
            func.coalesce(func.sum(column), 0),
            func.count(func.distinct(ProjectMonthlyRevenue.project_id)),
        )
        .join(Project, Project.id == ProjectMonthlyRevenue.project_id)
        .join(Customer, Customer.id == Project.customer_id)
        .group_by(ProjectMonthlyRevenue.month)
    )
    rows = {
        month: (Decimal(total or 0), count)
        for month, total, count in (await session.execute(apply_filters(stmt, filters)))
    }
    zero = Decimal("0.00")
    months = sorted(set(await month_columns(session)) | set(rows))
    month_totals = [
        MonthlyTotal(
            month=m,
            fiscal_year=fiscal_year(m),
            fiscal_period=fiscal_period(m),
            total=rows.get(m, (zero, 0))[0],
            project_count=rows.get(m, (zero, 0))[1],
        )
        for m in months
    ]
    by_year: dict[str, list[MonthlyTotal]] = defaultdict(list)
    for entry in month_totals:
        by_year[entry.fiscal_year].append(entry)

    count_stmt = select(func.count()).select_from(
        apply_filters(
            select(Project.id).join(Customer, Customer.id == Project.customer_id), filters
        ).subquery()
    )
    return TotalsResponse(
        basis=basis,
        months=month_totals,
        fiscal_years=[
            FiscalYearTotal(
                fiscal_year=year,
                total=sum((e.total for e in entries), zero),
                months_with_data=sum(1 for e in entries if e.project_count),
            )
            for year, entries in sorted(by_year.items())
        ],
        grand_total=sum((e.total for e in month_totals), zero),
        project_count=await session.scalar(count_stmt) or 0,
    )
