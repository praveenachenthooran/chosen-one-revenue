"""Import, idempotency, audit and reconciliation tests (real PostgreSQL)."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.migration.loader import ImportReport, import_workbook
from app.migration.workbook import parse_workbook
from app.models import (
    Category,
    Customer,
    ImportRun,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    RevenueRevision,
    User,
)
from tests.workbook_builder import MONTHS, WorkbookBuilder


async def run_import(session: AsyncSession, builder: WorkbookBuilder) -> ImportReport:
    return await import_workbook(session, parse_workbook(builder.build()))


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


def sample(builder: WorkbookBuilder) -> WorkbookBuilder:
    builder.add("R1", Customer="Acme", Project="Portal", amounts=[1000.0, 2000.0, 3000.0])
    builder.add("R2", Customer="Acme", Project="Mobile", AE="Shah", amounts=[None, 500.5, 500.5])
    builder.add("R3", Customer="Globex", Project="Core", **{"Commercial Owner": "AF"})
    return builder


async def test_import_creates_projects_revenue_owners_and_reference_data(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    report = await run_import(session, sample(builder))

    assert (report.projects_created, report.projects_updated, report.rows_rejected) == (3, 0, 0)
    assert await count(session, Project) == 3
    assert await count(session, Customer) == 2
    assert await count(session, ProjectMonthlyRevenue) == 8  # one blank cell stays blank
    assert {c.code for c in await session.scalars(select(Category))} == {"EE", "EN", "NN"}

    r2 = await session.scalar(
        select(Project)
        .where(Project.source_row_id == "R2")
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            joinedload(Project.customer),
        )
    )
    assert r2 is not None
    assert {o.role: o.user.code for o in r2.owners} == {"client_partner": "AC", "ae": "Shah"}
    assert r2.customer.name == "Acme"
    assert r2.source_row_number == 4


async def test_imported_users_are_not_authorized_application_users(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    await run_import(session, sample(builder))
    users = list(await session.scalars(select(User).order_by(User.code)))
    assert len(users) == 5
    assert not any(u.is_provisioned for u in users)
    priya = next(u for u in users if u.code == "PN")
    await session.refresh(priya, ["roles"])
    assert [r.name for r in priya.roles] == ["Client Partner", "Commercial Owner"]


async def test_repeat_import_is_idempotent(session: AsyncSession, builder: WorkbookBuilder) -> None:
    await run_import(session, sample(builder))
    before = (
        await count(session, Project),
        await count(session, ProjectMonthlyRevenue),
        await count(session, Customer),
        await count(session, User),
        await count(session, RevenueRevision),
    )

    again = await run_import(session, builder)
    await run_import(session, builder)

    after = (
        await count(session, Project),
        await count(session, ProjectMonthlyRevenue),
        await count(session, Customer),
        await count(session, User),
        await count(session, RevenueRevision),
    )
    assert after == before
    assert (again.projects_created, again.projects_updated, again.projects_unchanged) == (0, 0, 3)
    assert await count(session, ImportRun) == 3  # every run is still recorded


async def test_changed_source_amount_updates_in_place_and_writes_an_audit_event(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    await run_import(session, sample(builder))
    builder.rows[0]["amounts"] = [1000.0, 2500.25, None]  # change Nov, blank Dec
    report = await run_import(session, builder)

    assert (report.projects_updated, report.projects_unchanged) == (1, 2)
    assert await count(session, Project) == 3
    project = await session.scalar(select(Project).where(Project.source_row_id == "R1"))
    assert project is not None
    amounts = {
        r.month: r.amount
        for r in await session.scalars(
            select(ProjectMonthlyRevenue).where(ProjectMonthlyRevenue.project_id == project.id)
        )
    }
    assert amounts == {MONTHS[0]: Decimal("1000.00"), MONTHS[1]: Decimal("2500.25")}

    events = list(
        await session.scalars(
            select(RevenueRevision)
            .where(
                RevenueRevision.project_id == project.id, RevenueRevision.change_type != "create"
            )
            .order_by(RevenueRevision.month)
        )
    )
    assert [(e.change_type, e.month, e.old_value, e.new_value) for e in events] == [
        ("update", MONTHS[1], "2000.00", "2500.25"),
        ("delete", MONTHS[2], "3000.00", None),
    ]
    assert all(e.actor_label == "excel-import" and e.import_run_id for e in events)


async def test_rejected_rows_are_reported_and_not_imported(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    sample(builder).add("R4", amounts=[1.0, "N/A", 3.0]).add("R5", Status=None)
    report = await run_import(session, builder)

    assert report.rows_rejected == 2
    assert await count(session, Project) == 3
    rejected = {i.source_row_id: i.code for i in report.issues if i.row_rejected}
    assert rejected == {"R4": "invalid_amount", "R5": "missing_required_field"}


async def test_reconciliation_matches_source_to_the_cent(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    builder.add("R1", amounts=[0.10, 0.20, 1234567.89]).add("R2", amounts=[0.20, 0.10, 0.01])
    report = await run_import(session, builder)

    assert report.reconciled
    totals = {r.month: r.database_total for r in report.reconciliation}
    assert totals == {
        MONTHS[0]: Decimal("0.30"),  # exact; binary floats would give 0.30000000000000004
        MONTHS[1]: Decimal("0.30"),
        MONTHS[2]: Decimal("1234567.90"),
    }
    assert all(r.matches for r in report.reconciliation)


async def test_rejecting_a_row_that_carries_money_shows_as_a_variance(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    builder.add("R1", amounts=[100.0, 100.0, 100.0])
    builder.add("R2", Status=None, amounts=[50.0, 50.0, 50.0])
    report = await run_import(session, builder)

    assert not report.reconciled
    assert all(not r.matches for r in report.reconciliation)
    assert {r.source_total - r.database_total for r in report.reconciliation} == {Decimal("50")}


async def test_mismatch_with_excels_own_cached_totals_is_flagged(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    parsed = parse_workbook(sample(builder).build())
    parsed.excel_totals[MONTHS[0]] = Decimal("999.00")  # as if Excel had cached another figure
    report = await import_workbook(session, parsed)

    assert not report.reconciled
    flags = {r.month: r.matches for r in report.reconciliation}
    assert flags == {MONTHS[0]: False, MONTHS[1]: True, MONTHS[2]: True}


async def test_money_columns_are_exact_decimals(
    session: AsyncSession, builder: WorkbookBuilder
) -> None:
    builder.add("R1", amounts=[999999999999.99, 0.01, 0.0])
    await run_import(session, builder)
    row = await session.scalar(
        select(ProjectMonthlyRevenue).where(ProjectMonthlyRevenue.month == MONTHS[0])
    )
    assert row is not None
    assert isinstance(row.amount, Decimal) and row.amount == Decimal("999999999999.99")


async def test_failed_import_rolls_back_and_records_a_failed_run(
    session: AsyncSession, builder: WorkbookBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: object, **__: object) -> None:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.migration.loader._reconcile", boom)
    with pytest.raises(RuntimeError):
        await run_import(session, sample(builder))

    assert await count(session, Project) == 0
    assert await count(session, ProjectMonthlyRevenue) == 0
    runs = list(await session.scalars(select(ImportRun)))
    assert [r.status for r in runs] == ["failed"]
