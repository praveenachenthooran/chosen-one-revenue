"""The forecast's month columns, shared by the API and the pipeline."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportRun, ProjectMonthlyRevenue


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
