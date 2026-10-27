from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db_session
from app.fiscal import fiscal_year
from app.models import ImportRun
from app.schemas import ImportIssueOut, ImportRunDetail, ImportRunSummary, ReconciliationOut

router = APIRouter(prefix="/imports", tags=["imports"])
Session = Annotated[AsyncSession, Depends(get_db_session)]


def _detail(run: ImportRun) -> ImportRunDetail:
    summary = ImportRunSummary.model_validate(run)
    return ImportRunDetail(
        **summary.model_dump(),
        issues=[ImportIssueOut.model_validate(i) for i in run.issues],
        reconciliation=[
            ReconciliationOut(
                month=r.month,
                fiscal_year=fiscal_year(r.month),
                source_total=r.source_total,
                database_total=r.database_total,
                excel_reported_total=r.excel_reported_total,
                variance=r.database_total - r.source_total,
                matches=r.matches,
            )
            for r in run.reconciliation
        ],
    )


async def _run(session: AsyncSession, *conditions: ColumnElement[bool]) -> ImportRun:
    run = await session.scalar(
        select(ImportRun)
        .where(*conditions)
        .order_by(ImportRun.id.desc())
        .options(selectinload(ImportRun.issues), selectinload(ImportRun.reconciliation))
        .limit(1)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Import run not found")
    return run


@router.get("", response_model=list[ImportRunSummary])
async def list_imports(session: Session) -> list[ImportRun]:
    """The 20 most recent import runs, newest first."""
    rows = await session.scalars(select(ImportRun).order_by(ImportRun.id.desc()).limit(20))
    return list(rows)


@router.get("/latest", response_model=ImportRunDetail)
async def latest_import(session: Session) -> ImportRunDetail:
    """Latest completed import: counts, rejected rows, warnings and monthly reconciliation."""
    return _detail(await _run(session, ImportRun.status == "completed"))


@router.get("/{run_id}", response_model=ImportRunDetail)
async def get_import(run_id: int, session: Session) -> ImportRunDetail:
    return _detail(await _run(session, ImportRun.id == run_id))
