"""End-to-end check against the supplied workbook (skipped where `reference/` is absent)."""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.migration.loader import import_workbook
from app.migration.workbook import parse_workbook
from app.models import Customer, Project, ProjectMonthlyRevenue

WORKBOOK = (
    Path(__file__).resolve().parents[2] / "reference/03-demo-data/Chosen_One_Dummy_Data_FY27.xlsx"
)
pytestmark = pytest.mark.skipif(not WORKBOOK.is_file(), reason="reference workbook not present")


async def test_supplied_workbook_imports_and_reconciles_to_the_dollar(
    session: AsyncSession,
) -> None:
    digest = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
    parsed = parse_workbook(WORKBOOK)
    report = await import_workbook(session, parsed)

    assert (report.rows_read, report.projects_created, report.rows_rejected) == (111, 111, 0)
    assert report.reconciled and len(report.reconciliation) == 24
    assert all(r.matches and r.excel_reported_total is not None for r in report.reconciliation)

    by_month = {r.month: r for r in report.reconciliation}
    assert by_month[date(2025, 10, 1)].database_total == Decimal("2324004.00")
    assert by_month[date(2026, 12, 1)].database_total == Decimal("2354000.00")  # holiday dip
    assert by_month[date(2027, 9, 1)].database_total == Decimal("2028250.00")

    assert await session.scalar(select(func.count()).select_from(Project)) == 111
    assert await session.scalar(select(func.count()).select_from(Customer)) == 53
    assert await session.scalar(select(func.count()).select_from(ProjectMonthlyRevenue)) == 2287
    assert await session.scalar(select(func.sum(ProjectMonthlyRevenue.amount))) == Decimal(
        "58499798.00"
    )

    assert sorted((i.source_row_id, i.code) for i in report.issues) == [
        ("R1003", "missing_client_partner"),
        ("R1010", "possible_duplicate_project"),
        ("R1014", "missing_client_partner"),
    ]
    assert hashlib.sha256(WORKBOOK.read_bytes()).hexdigest() == digest  # source untouched


async def test_supplied_workbook_reimport_changes_nothing(session: AsyncSession) -> None:
    parsed = parse_workbook(WORKBOOK)
    await import_workbook(session, parsed)
    again = await import_workbook(session, parsed)

    assert (again.projects_created, again.projects_updated, again.projects_unchanged) == (0, 0, 111)
    assert await session.scalar(select(func.count()).select_from(Project)) == 111
    assert await session.scalar(select(func.count()).select_from(ProjectMonthlyRevenue)) == 2287
