"""Snapshots: exact contents, immutability, consistency after later edits, and the monthly job."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import (
    ForecastSnapshot,
    ForecastSnapshotLine,
    ForecastSnapshotMonth,
    JobRun,
    Project,
    ProjectMonthlyRevenue,
)
from app.pipeline.forecast import DealFilters
from app.snapshots import jobs
from app.snapshots.calendar import first_business_day, is_business_day
from app.snapshots.jobs import run_scheduled_snapshot
from app.snapshots.service import (
    MANUAL,
    SCHEDULED,
    filtered_months,
    load_lines,
    snapshot_months,
    take_snapshot,
    verify_snapshot,
)
from tests.pipeline_helpers import sdeal
from tests.reporting_helpers import MON_NOV_2, project_id, set_amount, take
from tests.test_pipeline_api import Env, env  # noqa: F401  # pyright: ignore[reportUnusedImport]

OCT, NOV, DEC = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)


async def months_of(
    session: AsyncSession, snapshot: ForecastSnapshot
) -> dict[date, tuple[str, str]]:
    return {
        m.month: (str(m.committed), str(m.weighted))
        for m in await snapshot_months(session, snapshot.id)
    }


# ---- business-day calendar ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 10, date(2026, 10, 1)),  # Thursday
        (2026, 11, date(2026, 11, 2)),  # the 1st is a Sunday
        (2026, 8, date(2026, 8, 3)),  # the 1st is a Saturday
        (2027, 1, date(2027, 1, 1)),  # Friday (no holiday calendar)
        (2027, 5, date(2027, 5, 3)),  # the 1st is a Saturday
    ],
)
def test_first_business_day_skips_weekends(year: int, month: int, expected: date) -> None:
    assert first_business_day(year, month) == expected


def test_first_business_day_is_always_a_weekday_in_the_month() -> None:
    for year in (2026, 2027, 2028):
        for month in range(1, 13):
            day = first_business_day(year, month)
            assert is_business_day(day) and (day.year, day.month) == (year, month)
            assert day.day <= 3


# ---- contents -----------------------------------------------------------------------------


async def test_snapshot_stores_exact_committed_weighted_and_combined(env: Env) -> None:
    await env.load([sdeal("1")])  # PRD example: $120k at 50% from Nov for 6 months
    snapshot = await take(env.session, MON_NOV_2)

    assert (snapshot.committed_total, snapshot.weighted_total, snapshot.combined_total) == (
        Decimal("18000.00"),
        Decimal("60000.00"),
        Decimal("78000.00"),
    )
    months = await months_of(env.session, snapshot)
    assert months[OCT] == ("3000.00", "0.00")
    assert months[NOV] == ("6000.00", "10000.00")
    assert months[date(2027, 4, 1)] == ("0.00", "10000.00")
    stored = await snapshot_months(env.session, snapshot.id)
    assert all(m.total == m.committed + m.weighted for m in stored)
    assert sum((m.total for m in stored), Decimal(0)) == snapshot.combined_total
    assert (snapshot.project_count, snapshot.deal_count, snapshot.kind) == (3, 1, MANUAL)
    assert snapshot.period_month == NOV and snapshot.taken_at == MON_NOV_2
    assert len(snapshot.checksum) == 64 and await verify_snapshot(env.session, snapshot)


async def test_snapshot_lines_carry_the_attributes_they_had_then(env: Env) -> None:
    await env.load([sdeal("1", company="Initech", practice="CRM", region="UK")])
    snapshot = await take(env.session, MON_NOV_2)
    lines = await load_lines(env.session, snapshot.id)

    committed = {
        ln.reference: ln for ln in lines if ln.line_type == "committed" and ln.month == OCT
    }
    assert committed["R2"].customer == "Granitepeak Telecom"
    assert committed["R2"].owner_codes == ["JM"] and committed["R2"].industry == "Broad Markets"
    assert committed["R3"].category == "EN" and committed["R3"].region == "UK"
    assert committed["R3"].practice == "CRM"
    deal = [ln for ln in lines if ln.line_type == "weighted"]
    assert len(deal) == 6 and {ln.reference for ln in deal} == {"1"}
    assert deal[0].stage == "Negotiation/Review" and deal[0].probability == Decimal("0.5000")
    assert deal[0].owner_codes == ["Shah"] and deal[0].customer == "Initech"
    assert snapshot.stage_probabilities  # the probabilities used are stored with it
    assert snapshot.import_run_id is not None and snapshot.sync_run_id is not None


async def test_draft_projects_are_not_committed_and_linked_deals_are_not_double_counted(
    env: Env,
) -> None:
    await env.load([sdeal("5", stage="Closed Won", company="Initech", name="Won Deal")])
    snapshot = await take(env.session, MON_NOV_2)
    lines = await load_lines(env.session, snapshot.id)
    # The unconfirmed draft counts once, as pipeline at 100%, and not as committed.
    assert [ln.reference for ln in lines if ln.line_type == "committed"].count("HS-5") == 0
    assert sum(ln.amount for ln in lines if ln.line_type == "weighted") == Decimal("120000.00")
    assert snapshot.committed_total == Decimal("18000.00")


async def test_a_snapshot_ignores_uncommitted_work_in_flight(env: Env, engine: AsyncEngine) -> None:
    pid = await project_id(env.session, "R1")
    other = async_sessionmaker(engine, expire_on_commit=False)()
    async with other:
        await other.execute(
            update(ProjectMonthlyRevenue)
            .where(ProjectMonthlyRevenue.project_id == pid, ProjectMonthlyRevenue.month == OCT)
            .values(amount=Decimal("999999.00"))
        )  # not committed
        snapshot = await take(env.session, MON_NOV_2)
        await other.rollback()
    assert (await months_of(env.session, snapshot))[OCT] == ("3000.00", "0.00")


# ---- immutability -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE forecast_snapshots SET note = 'x'",
        "DELETE FROM forecast_snapshots",
        "UPDATE forecast_snapshot_lines SET amount = 1",
        "DELETE FROM forecast_snapshot_lines",
        "UPDATE forecast_snapshot_months SET total = 1",
        "DELETE FROM forecast_snapshot_months",
    ],
)
async def test_the_database_rejects_any_change_to_a_snapshot(env: Env, statement: str) -> None:
    await take(env.session, MON_NOV_2)
    with pytest.raises(DBAPIError, match="immutable"):
        await env.session.execute(text(statement))
    await env.session.rollback()
    assert await env.session.scalar(select(ForecastSnapshot.id)) is not None


async def test_tampering_is_detected_by_the_checksum(env: Env) -> None:
    snapshot = await take(env.session, MON_NOV_2)
    assert await verify_snapshot(env.session, snapshot)
    # Only a database owner can do this; the checksum still exposes it.
    for statement in (
        "ALTER TABLE forecast_snapshot_lines DISABLE TRIGGER forecast_snapshot_lines_immutable",
        "UPDATE forecast_snapshot_lines SET amount = amount + 1 "
        "WHERE id = (SELECT min(id) FROM forecast_snapshot_lines)",
        "ALTER TABLE forecast_snapshot_lines ENABLE TRIGGER forecast_snapshot_lines_immutable",
    ):
        await env.session.execute(text(statement))
    await env.session.commit()
    assert not await verify_snapshot(env.session, snapshot)


async def test_one_scheduled_snapshot_per_month_but_manual_ones_are_unlimited(env: Env) -> None:
    await take(env.session, MON_NOV_2, kind=SCHEDULED)
    with pytest.raises(IntegrityError):
        await take(env.session, MON_NOV_2 + timedelta(hours=1), kind=SCHEDULED)
    await env.session.rollback()
    await take(env.session, MON_NOV_2 + timedelta(hours=2))
    await take(env.session, MON_NOV_2 + timedelta(hours=3))
    count = len(list(await env.session.scalars(select(ForecastSnapshot))))
    assert count == 3


# ---- historical consistency after edits ---------------------------------------------------


async def test_later_edits_renames_and_resyncs_never_change_a_snapshot(env: Env) -> None:
    await env.load([sdeal("1")])
    first = await take(env.session, MON_NOV_2)
    before_months = await months_of(env.session, first)
    before_lines = [(ln.reference, ln.month, ln.amount, ln.customer, ln.name, ln.owner_codes)
                    for ln in await load_lines(env.session, first.id)]  # fmt: skip

    # Edit revenue, rename a project and its customer's industry, remove a deal, add a deal.
    await set_amount(env.session, "R1", OCT, "777.00")
    await env.session.execute(
        update(Project)
        .where(Project.source_row_id == "R2")
        .values(name="Renamed", industry="Other")
    )
    await env.session.commit()
    await env.load([sdeal("2", amount="10000", start=date(2026, 12, 1), duration=2)])

    assert await months_of(env.session, first) == before_months
    after_lines = [(ln.reference, ln.month, ln.amount, ln.customer, ln.name, ln.owner_codes)
                   for ln in await load_lines(env.session, first.id)]  # fmt: skip
    assert after_lines == before_lines
    assert await verify_snapshot(env.session, first)

    second = await take(env.session, MON_NOV_2 + timedelta(days=1))
    later = await months_of(env.session, second)
    assert later[OCT] == ("2777.00", "0.00") and later != before_months
    assert second.id != first.id and await verify_snapshot(env.session, second)


async def test_filtered_views_of_a_snapshot_are_reproducible_after_changes(env: Env) -> None:
    await env.load([sdeal("1", company="Initech", practice="CRM", region="UK")])
    snapshot = await take(env.session, MON_NOV_2)

    def view(**kw: str) -> DealFilters:
        return DealFilters(**kw)

    first = {
        "uk": await filtered_months(env.session, snapshot, view(region="UK")),
        "jm": await filtered_months(env.session, snapshot, view(owner="JM")),
        "en": await filtered_months(env.session, snapshot, view(category="EN")),
    }
    await set_amount(env.session, "R3", NOV, "1.00")  # changes live data, not the snapshot
    for key, kwargs in {
        "uk": {"region": "UK"},
        "jm": {"owner": "JM"},
        "en": {"category": "EN"},
    }.items():
        again = await filtered_months(env.session, snapshot, view(**kwargs))
        assert [(m.month, m.committed, m.weighted) for m in again] == [
            (m.month, m.committed, m.weighted) for m in first[key]
        ]
    uk_nov = next(m for m in first["uk"] if m.month == NOV)
    assert (uk_nov.committed, uk_nov.weighted) == (Decimal("2000.00"), Decimal("10000.00"))
    assert next(m for m in first["jm"] if m.month == NOV).weighted == Decimal("0.00")


async def test_unfiltered_totals_equal_the_sum_of_the_stored_lines(env: Env) -> None:
    await env.load([sdeal("1"), sdeal("2", amount="33333.33", duration=7, stage="Contracting")])
    snapshot = await take(env.session, MON_NOV_2)
    lines = await load_lines(env.session, snapshot.id)
    stored = await snapshot_months(env.session, snapshot.id)
    for m in stored:
        assert m.committed == sum(
            (ln.amount for ln in lines if ln.line_type == "committed" and ln.month == m.month),
            Decimal(0),
        )
        assert m.weighted == sum(
            (ln.amount for ln in lines if ln.line_type == "weighted" and ln.month == m.month),
            Decimal(0),
        )
    # A filter that excludes nothing gives the stored figures back.
    everything = await filtered_months(env.session, snapshot, DealFilters(q="e"))
    assert [(m.month, m.committed) for m in everything] != []  # sanity: the filter ran
    assert await env.session.scalar(select(ForecastSnapshotLine.id).limit(1)) is not None
    assert await env.session.scalar(select(ForecastSnapshotMonth.month).limit(1)) is not None


# ---- the scheduled job --------------------------------------------------------------------


async def jobs_log(session: AsyncSession) -> list[tuple[str, str]]:
    rows = await session.scalars(select(JobRun).order_by(JobRun.id))
    return [(r.status, r.message) for r in rows]


async def scheduled_count(session: AsyncSession) -> int:
    rows = await session.scalars(select(ForecastSnapshot).where(ForecastSnapshot.kind == SCHEDULED))
    return len(list(rows))


async def test_job_waits_for_the_first_business_day_then_runs_once(env: Env) -> None:
    sunday = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # Nov 1 2026 is a Sunday
    first = await run_scheduled_snapshot(env.session, now=sunday)
    assert first.status == "skipped" and "Monday 02 November" in first.message
    assert await scheduled_count(env.session) == 0

    monday = await run_scheduled_snapshot(env.session, now=MON_NOV_2)
    assert monday.status == "completed" and monday.snapshot_id is not None
    snapshot = await env.session.get(ForecastSnapshot, monday.snapshot_id)
    assert snapshot is not None and snapshot.kind == SCHEDULED and snapshot.period_month == NOV
    assert snapshot.taken_by_label == "scheduled job" and snapshot.taken_by_id is None

    again = await run_scheduled_snapshot(env.session, now=MON_NOV_2 + timedelta(hours=3))
    tuesday = await run_scheduled_snapshot(env.session, now=MON_NOV_2 + timedelta(days=1))
    assert (again.status, tuesday.status) == ("skipped", "skipped")
    assert again.snapshot_id == monday.snapshot_id
    assert await scheduled_count(env.session) == 1
    assert [s for s, _ in await jobs_log(env.session)] == [
        "skipped",
        "completed",
        "skipped",
        "skipped",
    ]


async def test_job_catches_up_if_the_first_business_day_was_missed(env: Env) -> None:
    wednesday = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    outcome = await run_scheduled_snapshot(env.session, now=wednesday)
    assert outcome.status == "completed"
    snapshot = await env.session.get(ForecastSnapshot, outcome.snapshot_id)
    assert snapshot is not None and snapshot.taken_at == wednesday  # the true time is recorded


async def test_a_manual_snapshot_does_not_stop_the_scheduled_one(env: Env) -> None:
    await take(env.session, MON_NOV_2 - timedelta(hours=1))
    outcome = await run_scheduled_snapshot(env.session, now=MON_NOV_2)
    assert outcome.status == "completed"


async def test_concurrent_job_runs_make_one_snapshot(env: Env, engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> str:
        async with factory() as session:
            return (await run_scheduled_snapshot(session, now=MON_NOV_2)).status

    statuses = sorted(await asyncio.gather(run(), run(), run()))
    assert statuses == ["completed", "skipped", "skipped"]
    assert await scheduled_count(env.session) == 1


async def test_a_failing_job_reports_and_leaves_no_partial_snapshot(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs, "take_snapshot", boom)
    outcome = await run_scheduled_snapshot(env.session, now=MON_NOV_2)
    assert outcome.status == "failed" and "database went away" in outcome.message
    assert await scheduled_count(env.session) == 0
    assert (await jobs_log(env.session))[-1][0] == "failed"

    monkeypatch.undo()  # a later run recovers
    assert (await run_scheduled_snapshot(env.session, now=MON_NOV_2)).status == "completed"


async def test_snapshot_creation_is_atomic(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    original = env.session.flush
    calls = 0

    async def flaky(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:  # after the snapshot row, while its lines are written
            raise RuntimeError("disk full")
        await original(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(env.session, "flush", flaky)
    with pytest.raises(RuntimeError):
        await take_snapshot(env.session, kind=MANUAL, now=MON_NOV_2)
    await env.session.rollback()
    monkeypatch.undo()
    assert await env.session.scalar(select(ForecastSnapshot.id)) is None
    assert await env.session.scalar(select(ForecastSnapshotLine.id)) is None
