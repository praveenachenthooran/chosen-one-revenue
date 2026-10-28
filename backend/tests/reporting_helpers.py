"""Helpers for snapshot, actuals and accuracy tests."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import openpyxl
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ForecastSnapshot, Project, ProjectMonthlyRevenue
from app.snapshots.service import MANUAL, take_snapshot

# 2026-11-02 is a Monday: the first business day of November 2026 (the 1st is a Sunday).
MON_NOV_2 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


async def project_id(session: AsyncSession, row_id: str) -> int:
    found = await session.scalar(select(Project.id).where(Project.source_row_id == row_id))
    assert found is not None, row_id
    return found


async def set_amount(session: AsyncSession, row_id: str, month: date, amount: str) -> None:
    pid = await project_id(session, row_id)
    await session.execute(
        update(ProjectMonthlyRevenue)
        .where(ProjectMonthlyRevenue.project_id == pid, ProjectMonthlyRevenue.month == month)
        .values(amount=Decimal(amount))
    )
    await session.commit()


async def take(
    session: AsyncSession, now: datetime, kind: str = MANUAL, note: str | None = None
) -> ForecastSnapshot:
    snapshot = await take_snapshot(session, kind=kind, now=now, actor_label="test", note=note)
    await session.commit()
    return snapshot


def csv_file(*rows: str, header: str = "Month,Customer,Project,Amount") -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def xlsx_file(rows: list[list[Any]], header: list[str] | None = None) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(header or ["Month", "Customer", "Project", "Amount"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
