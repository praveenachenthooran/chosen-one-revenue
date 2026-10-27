"""End-to-end against both supplied workbooks (skipped where `reference/` is absent)."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.migration.loader import import_workbook
from app.migration.workbook import parse_workbook
from app.models import DraftConfirmation, HubspotOpportunity, Project, ProjectMonthlyRevenue
from app.pipeline.forecast import aggregate, forecast_window, load_views
from app.pipeline.source import DEFAULT_WORKBOOK, WorkbookDealSource
from app.pipeline.sync import run_sync
from tests.helpers import make_settings

CHOSEN_ONE = (
    Path(__file__).resolve().parents[2] / "reference/03-demo-data/Chosen_One_Dummy_Data_FY27.xlsx"
)
pytestmark = pytest.mark.skipif(
    not (CHOSEN_ONE.is_file() and DEFAULT_WORKBOOK.is_file()),
    reason="reference workbooks not present",
)
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


async def load_everything(session: AsyncSession):  # noqa: ANN201
    await import_workbook(session, parse_workbook(CHOSEN_ONE))
    return await run_sync(session, WorkbookDealSource(DEFAULT_WORKBOOK), make_settings(), now=NOW)


def preview_totals() -> dict[date, Decimal]:
    """Monthly totals of the workbook's own formula-driven Weighted_Preview (cached values)."""
    sheet = openpyxl.load_workbook(DEFAULT_WORKBOOK, data_only=True)["Weighted_Preview"]
    header = [c.value for c in sheet[1]]
    months = {i: h.date() for i, h in enumerate(header) if isinstance(h, datetime)}
    totals = {m: Decimal(0) for m in months.values()}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row[0] is None or not str(row[0]).isdigit():
            continue
        for i, month in months.items():
            totals[month] += Decimal(repr(row[i] or 0))
    return totals


async def test_the_supplied_hubspot_export_syncs_and_follows_the_prd(session: AsyncSession) -> None:
    report = await load_everything(session)

    assert (report.status, report.deals_seen, report.deals_created) == ("completed", 53, 53)
    assert report.drafts_created == 1  # Brightharbor: Closed Won, no Chosen One row (story H4)
    views = {
        v.deal.hubspot_deal_id: v for v in await load_views(session, await forecast_window(session))
    }

    # H3: already in the Chosen One -> linked, no draft, no weighted line, counted once.
    for deal_id, row_id in (("57018801551", "R1007"), ("64360839985", "R1003")):
        view = views[deal_id]
        assert view.status == "linked" and view.weighted_amount == Decimal("0.00")
        assert view.linked_project is not None and view.linked_project.source_row_id == row_id
    # H4: the missing Closed Won gets a draft (not committed) and keeps counting until confirmed.
    brightharbor = views["65394918517"]
    assert brightharbor.status == "awaiting_confirmation"
    assert brightharbor.linked_project is not None and brightharbor.linked_project.status == "Draft"
    assert brightharbor.weighted_amount == Decimal("186000.00")
    # H5: Closed Lost deals contribute nothing.
    assert (
        views["58566016121"].status == "closed_lost"
        and views["61248544528"].status == "closed_lost"
    )
    assert sum(v.weighted_amount for v in views.values() if v.status == "closed_lost") == 0
    # H9: deals without a start or duration are reported, not phased.
    for deal_id in ("45297778568", "53011760873"):
        phasing = views[deal_id].phasing
        assert phasing is not None and not phasing.phaseable
    # H7: look-alike deals are flagged before both are weighted.
    codes = {(f.deal_id, f.code) for f in report.findings}
    assert ("49276322293", "possible_duplicate_deal") in codes
    assert ("58834202414", "possible_duplicate_deal") in codes


async def test_weighted_pipeline_matches_the_workbooks_own_preview(session: AsyncSession) -> None:
    """The preview sheet exists to 'check the app to the dollar'. Differences are cent rounding."""
    await load_everything(session)
    window = await forecast_window(session)
    views = await load_views(session, window)
    ours = aggregate([v for v in views if v.status == "weighted"]).weighted_by_month
    preview = preview_totals()

    assert set(preview) <= set(ours)
    for month, expected in preview.items():
        assert abs(ours[month] - expected) < Decimal("0.05"), month
        assert abs(ours[month] - expected) < Decimal("1"), month  # and so, to the dollar

    # Brightharbor is the one extra: Closed Won at 100% until confirmed (186,000 over 6 months).
    with_won = aggregate([v for v in views if v.counts]).weighted_by_month
    for month in (date(2026, 11, 1), date(2027, 4, 1)):
        assert with_won[month] - ours[month] == Decimal("31000.00")
    assert with_won[date(2026, 10, 1)] == ours[date(2026, 10, 1)]


async def test_resyncing_the_supplied_export_changes_nothing(session: AsyncSession) -> None:
    await load_everything(session)
    before = (
        await session.scalar(select(func.count()).select_from(HubspotOpportunity)),
        await session.scalar(select(func.count()).select_from(Project)),
        await session.scalar(select(func.count()).select_from(DraftConfirmation)),
        await session.scalar(select(func.sum(ProjectMonthlyRevenue.amount))),
    )
    again = await run_sync(session, WorkbookDealSource(DEFAULT_WORKBOOK), make_settings(), now=NOW)

    assert (again.deals_created, again.deals_updated, again.deals_unchanged) == (0, 0, 53)
    assert again.drafts_created == 0
    assert before == (
        await session.scalar(select(func.count()).select_from(HubspotOpportunity)),
        await session.scalar(select(func.count()).select_from(Project)),
        await session.scalar(select(func.count()).select_from(DraftConfirmation)),
        await session.scalar(select(func.sum(ProjectMonthlyRevenue.amount))),
    )


async def test_the_chosen_one_import_baseline_is_untouched_by_a_pipeline_sync(
    session: AsyncSession,
) -> None:
    await load_everything(session)
    assert await session.scalar(select(func.sum(ProjectMonthlyRevenue.baseline_amount))) == Decimal(
        "58499798.00"
    )
    # The draft's schedule is not part of the imported baseline (its baseline is NULL).
    draft_rows = await session.scalar(
        select(func.count())
        .select_from(ProjectMonthlyRevenue)
        .join(Project, Project.id == ProjectMonthlyRevenue.project_id)
        .where(Project.status == "Draft", ProjectMonthlyRevenue.baseline_amount.is_not(None))
    )
    assert draft_rows == 0
