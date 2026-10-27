"""Shared query pieces. All financial aggregation happens here, in SQL, never in the browser."""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Customer,
    ImportIssue,
    ImportRun,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    User,
)


@dataclass
class ProjectFilters:
    q: str | None = None
    status: str | None = None
    practice: str | None = None
    region: str | None = None
    industry: str | None = None
    category: str | None = None
    customer: str | None = None
    owner: str | None = None
    needs_attention: bool | None = None


def get_project_filters(
    q: Annotated[str | None, Query(description="Search customer, project or Row ID")] = None,
    status: str | None = None,
    practice: Annotated[str | None, Query(description="TECH / practice")] = None,
    region: str | None = None,
    industry: str | None = None,
    category: str | None = None,
    customer: str | None = None,
    owner: Annotated[str | None, Query(description="Owner user code, any role")] = None,
    needs_attention: bool | None = None,
) -> ProjectFilters:
    return ProjectFilters(
        q, status, practice, region, industry, category, customer, owner, needs_attention
    )


Filters = Annotated[ProjectFilters, Depends(get_project_filters)]

latest_run_id = (
    select(func.max(ImportRun.id)).where(ImportRun.status == "completed").scalar_subquery()
)

attention_count = (
    select(func.count(ImportIssue.id))
    .where(
        ImportIssue.run_id == latest_run_id,
        ImportIssue.source_row_id == Project.source_row_id,
        ImportIssue.severity == "warning",
    )
    .correlate(Project)
    .scalar_subquery()
)

total_revenue = (
    select(func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0))
    .where(ProjectMonthlyRevenue.project_id == Project.id)
    .correlate(Project)
    .scalar_subquery()
)


def apply_filters[S: Select[*tuple[Any, ...]]](stmt: S, f: ProjectFilters) -> S:
    """Filter a statement that already selects from Project joined to Customer."""
    if f.q:
        pattern = f"%{f.q.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.name.ilike(pattern),
                Project.name.ilike(pattern),
                Project.source_row_id.ilike(pattern),
            )
        )
    for column, value in (
        (Project.status, f.status),
        (Project.practice, f.practice),
        (Project.region, f.region),
        (Project.industry, f.industry),
        (Project.category_code, f.category),
        (Customer.name, f.customer),
    ):
        if value:
            stmt = stmt.where(column == value)
    if f.owner:
        stmt = stmt.where(
            exists().where(
                ProjectOwner.project_id == Project.id,
                ProjectOwner.user_id == User.id,
                User.code == f.owner,
            )
        )
    if f.needs_attention is True:
        stmt = stmt.where(attention_count > 0)
    elif f.needs_attention is False:
        stmt = stmt.where(attention_count == 0)
    return stmt


async def month_columns(session: AsyncSession) -> list[date]:
    """Fiscal months to display: the latest import's columns, else months that hold data."""
    run = await session.scalar(
        select(ImportRun)
        .where(ImportRun.status == "completed")
        .order_by(ImportRun.id.desc())
        .limit(1)
    )
    if run and run.month_columns:
        return [date.fromisoformat(m) for m in run.month_columns]
    rows = await session.scalars(
        select(ProjectMonthlyRevenue.month).distinct().order_by(ProjectMonthlyRevenue.month)
    )
    return list(rows)
