"""The monthly snapshot job (run by Railway cron, or by hand with `python -m app.snapshots`).

Run it every weekday. It is safe to run any number of times: it takes the month's scheduled
snapshot only on or after that month's first business day, and only if one does not exist yet.
Running late (a missed cron day) therefore catches up, and the snapshot records its true
`taken_at`. Every invocation, including the ones that do nothing, is written to `job_runs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ForecastSnapshot, JobRun
from app.snapshots.calendar import first_business_day, month_start
from app.snapshots.service import SCHEDULED, acquire_snapshot_lock, take_snapshot

JOB_NAME = "monthly_snapshot"


@dataclass
class JobOutcome:
    status: str  # completed | skipped | failed
    message: str
    snapshot_id: int | None = None


async def _record(
    session: AsyncSession,
    started: datetime,
    outcome: JobOutcome,
) -> None:
    session.add(
        JobRun(
            job=JOB_NAME,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=outcome.status,
            message=outcome.message,
            snapshot_id=outcome.snapshot_id,
        )
    )
    await session.commit()


async def run_scheduled_snapshot(
    session: AsyncSession, *, now: datetime | None = None
) -> JobOutcome:
    started = now or datetime.now(UTC)
    today = started.astimezone(UTC).date()
    period = month_start(today)
    due = first_business_day(period.year, period.month)
    try:
        await acquire_snapshot_lock(session)
        if today < due:
            outcome = JobOutcome(
                "skipped",
                f"Not due: the first business day of {period:%B %Y} is {due:%A %d %B}.",
            )
            await _record(session, started, outcome)
            return outcome
        existing = await session.scalar(
            select(ForecastSnapshot.id).where(
                ForecastSnapshot.kind == SCHEDULED, ForecastSnapshot.period_month == period
            )
        )
        if existing is not None:
            outcome = JobOutcome(
                "skipped",
                f"The {period:%B %Y} scheduled snapshot already exists (#{existing}).",
                existing,
            )
            await _record(session, started, outcome)
            return outcome
        snapshot = await take_snapshot(
            session, kind=SCHEDULED, now=started, actor_label="scheduled job"
        )
        outcome = JobOutcome(
            "completed",
            f"Took the {period:%B %Y} snapshot #{snapshot.id}: {snapshot.line_count} lines, "
            f"combined total {snapshot.combined_total}.",
            snapshot.id,
        )
        await _record(session, started, outcome)  # same transaction as the snapshot itself
        return outcome
    except IntegrityError:  # another instance won the race for this month
        await session.rollback()
        outcome = JobOutcome("skipped", f"The {period:%B %Y} snapshot was taken concurrently.")
    except Exception as exc:  # noqa: BLE001 - the job must report, not crash, on any failure
        await session.rollback()
        outcome = JobOutcome("failed", f"{type(exc).__name__}: {exc}")
    await _record(session, started, outcome)
    return outcome
