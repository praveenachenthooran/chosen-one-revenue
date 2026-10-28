"""The nightly QA job (run by Railway cron: `/opt/venv/bin/python -m app.qa nightly`)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import JobRun
from app.qa.engine import QaResult, run_qa_safely

JOB_NAME = "nightly_qa"


async def run_nightly(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None
) -> QaResult:
    """Run every check, waiting if another run is in progress. Always records a job_runs row."""
    started = now or datetime.now(UTC)
    result = await run_qa_safely(session, settings, trigger="scheduled", now=started, blocking=True)
    if result.status == "completed":
        message = (
            f"QA run #{result.run_id}: {result.new} new, {result.reopened} reopened, "
            f"{result.resolved} resolved, {result.open_total} open."
        )
    else:
        message = f"QA run #{result.run_id} failed: {result.error}"
    session.add(
        JobRun(
            job=JOB_NAME, started_at=started, finished_at=datetime.now(UTC),
            status="completed" if result.status == "completed" else "failed",
            message=message[:2000],
        )
    )  # fmt: skip
    await session.commit()
    return result
