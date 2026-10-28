"""Taking and reading immutable forecast snapshots.

A snapshot stores the unfiltered company forecast exactly as it stood at one instant: every
committed project-month and every weighted deal-month, with the attributes (customer, owner,
practice, region, ...) those rows had then, plus the monthly totals. Filters are applied when a
snapshot is *read*, to the stored lines, so any filtered historical view can be reproduced and no
later edit, rename or re-categorisation can change it. PostgreSQL triggers (migration 0005) reject
every UPDATE and DELETE on the snapshot tables.

The forecast is read on its own connection at REPEATABLE READ, so the snapshot is internally
consistent even while people edit, without blocking them.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from app.fiscal import FISCAL_YEAR_START_MONTH
from app.locks import SNAPSHOT_LOCK_KEY
from app.models import (
    ForecastSnapshot,
    ForecastSnapshotLine,
    ForecastSnapshotMonth,
    HubspotStage,
    HubspotSyncRun,
    ImportRun,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    User,
)
from app.pipeline.drafts import DRAFT_STATUS
from app.pipeline.forecast import DealFilters, aggregate, forecast_window, load_views
from app.snapshots.calendar import month_start

ZERO = Decimal("0.00")
PROBABILITY_QUANTUM = Decimal("0.0001")
SCHEDULED, MANUAL = "scheduled", "manual"


class SnapshotError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Line:
    line_type: str
    month: date
    amount: Decimal
    reference: str
    name: str
    customer: str | None
    project_id: int | None = None
    opportunity_id: int | None = None
    practice: str | None = None
    region: str | None = None
    industry: str | None = None
    category: str | None = None
    status: str | None = None
    owner_codes: list[str] = field(default_factory=lambda: [])
    stage: str | None = None
    probability: Decimal | None = None


@dataclass
class Collected:
    lines: list[Line]
    months: dict[date, tuple[Decimal, Decimal]]  # month -> (committed, weighted)
    as_of: date | None
    import_run_id: int | None
    sync_run_id: int | None
    stages: list[dict[str, Any]]
    unphased_deals: int
    unphased_weighted: Decimal
    outside_window_weighted: Decimal
    project_count: int
    deal_count: int


async def collect_forecast(reader: AsyncSession) -> Collected:
    window = await forecast_window(reader)
    views = await load_views(reader, window)

    projects = {
        p.id: p
        for p in await reader.scalars(
            select(Project)
            .where(Project.status != DRAFT_STATUS)
            .options(
                selectinload(Project.customer),
                selectinload(Project.owners).selectinload(ProjectOwner.user),
            )
        )
    }
    lines: list[Line] = []
    committed: dict[date, Decimal] = defaultdict(lambda: ZERO)
    seen_projects: set[int] = set()
    rows = await reader.execute(
        select(
            ProjectMonthlyRevenue.project_id,
            ProjectMonthlyRevenue.month,
            ProjectMonthlyRevenue.amount,
        )
        .where(ProjectMonthlyRevenue.amount != 0)
        .order_by(ProjectMonthlyRevenue.project_id, ProjectMonthlyRevenue.month)
    )
    for project_id, month, amount in rows:
        project = projects.get(project_id)
        if project is None:  # an unconfirmed draft: not committed revenue yet
            continue
        seen_projects.add(project_id)
        committed[month] += amount
        lines.append(
            Line(
                "committed", month, amount, project.source_row_id, project.name,
                project.customer.name, project_id=project_id, practice=project.practice,
                region=project.region, industry=project.industry, category=project.category_code,
                status=project.status,
                owner_codes=sorted(o.user.code for o in project.owners if o.user.code),
            )
        )  # fmt: skip

    weighted: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for view in views:
        if not view.counts or view.phasing is None or not view.phasing.phaseable:
            continue
        deal = view.deal
        project = view.linked_project
        owner_codes = [view.owner.code] if view.owner and view.owner.code else []
        for month, amount in sorted(view.phasing.monthly.items()):
            if amount == 0:
                continue
            weighted[month] += amount
            lines.append(
                Line(
                    "weighted", month, amount, deal.hubspot_deal_id, deal.name, view.company,
                    opportunity_id=deal.id, practice=deal.practice, region=deal.region,
                    industry=view.industry, category=project.category_code if project else None,
                    status=view.status, owner_codes=owner_codes,
                    stage=deal.stage,
                    probability=(view.probability or ZERO).quantize(PROBABILITY_QUANTUM),
                )
            )  # fmt: skip

    agg = aggregate(views)
    month_keys = sorted(set(window.months) | set(committed) | set(weighted))
    stages = [
        {
            "name": s.name,
            "probability": str(s.probability.quantize(PROBABILITY_QUANTUM)),
            "kind": s.kind,
            "in_forecast": s.in_forecast,
        }
        for s in await reader.scalars(select(HubspotStage).order_by(HubspotStage.position))
    ]
    return Collected(
        lines=lines,
        months={m: (committed.get(m, ZERO), weighted.get(m, ZERO)) for m in month_keys},
        as_of=window.as_of,
        import_run_id=await reader.scalar(
            select(func.max(ImportRun.id)).where(ImportRun.status == "completed")
        ),
        sync_run_id=await reader.scalar(
            select(func.max(HubspotSyncRun.id)).where(HubspotSyncRun.status == "completed")
        ),
        stages=stages,
        unphased_deals=agg.unphased_deals,
        unphased_weighted=agg.unphased_weighted,
        outside_window_weighted=agg.outside_window_weighted,
        project_count=len(seen_projects),
        deal_count=agg.deal_count,
    )


def compute_checksum(
    snapshot: ForecastSnapshot,
    lines: list[ForecastSnapshotLine],
    months: list[ForecastSnapshotMonth],
) -> str:
    """SHA-256 over everything that defines the snapshot, in a fixed order and format."""
    body = {
        "period_month": snapshot.period_month.isoformat(),
        "kind": snapshot.kind,
        "taken_at": snapshot.taken_at.astimezone(UTC).isoformat(),
        "as_of": snapshot.as_of.isoformat() if snapshot.as_of else None,
        "import_run_id": snapshot.import_run_id,
        "sync_run_id": snapshot.sync_run_id,
        "stages": snapshot.stage_probabilities,
        "totals": [
            str(snapshot.committed_total),
            str(snapshot.weighted_total),
            str(snapshot.combined_total),
            snapshot.unphased_deals,
            str(snapshot.unphased_weighted),
            str(snapshot.outside_window_weighted),
        ],
        "months": [
            [m.month.isoformat(), str(m.committed), str(m.weighted), str(m.total)]
            for m in sorted(months, key=lambda m: m.month)
        ],
        "lines": sorted(
            
                json.dumps(
                    [
                        ln.line_type, ln.month.isoformat(), str(ln.amount), ln.reference, ln.name,
                        ln.customer, ln.practice, ln.region, ln.industry, ln.category, ln.status,
                        sorted(ln.owner_codes), ln.stage,
                        str(ln.probability) if ln.probability is not None else None,
                    ]
                )
                for ln in lines
            
        ),
    }  # fmt: skip
    encoded = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def acquire_snapshot_lock(session: AsyncSession) -> None:
    """Serialise snapshot creation (held until the caller's transaction ends)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SNAPSHOT_LOCK_KEY})


async def take_snapshot(
    session: AsyncSession,
    *,
    kind: str,
    now: datetime,
    actor: User | None = None,
    actor_label: str | None = None,
    note: str | None = None,
) -> ForecastSnapshot:
    """Capture the forecast as it stands and add it to `session` (the caller commits).

    The forecast is read on a separate REPEATABLE READ connection, so what is stored is one
    consistent instant. The write is part of the caller's transaction: either the snapshot, its
    lines and its monthly totals all exist, or none do.
    """
    bind = session.bind
    if not isinstance(bind, AsyncEngine):  # pragma: no cover - always true for this app
        raise SnapshotError("no_engine", "The session is not bound to an engine.")
    await acquire_snapshot_lock(session)
    async with bind.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        async with AsyncSession(bind=connection, expire_on_commit=False) as reader:
            collected = await collect_forecast(reader)

    period = month_start(now.date())
    label = f"{period:%Y-%m} {kind}"
    if kind == MANUAL:
        label += f" ({now:%d %b %H:%M} UTC)"
    committed_total = sum((c for c, _ in collected.months.values()), ZERO)
    weighted_total = sum((w for _, w in collected.months.values()), ZERO)
    snapshot = ForecastSnapshot(
        label=label,
        period_month=period,
        kind=kind,
        taken_at=now,
        taken_by_id=actor.id if actor else None,
        taken_by_label=actor_label or (actor.name if actor else "scheduler"),
        note=note,
        as_of=collected.as_of,
        import_run_id=collected.import_run_id,
        sync_run_id=collected.sync_run_id,
        stage_probabilities=collected.stages,
        fiscal_year_start_month=FISCAL_YEAR_START_MONTH,
        committed_total=committed_total,
        weighted_total=weighted_total,
        combined_total=committed_total + weighted_total,
        unphased_deals=collected.unphased_deals,
        unphased_weighted=collected.unphased_weighted,
        outside_window_weighted=collected.outside_window_weighted,
        project_count=collected.project_count,
        deal_count=collected.deal_count,
        line_count=len(collected.lines),
        checksum="",
    )
    line_rows = [
        ForecastSnapshotLine(
            line_type=ln.line_type, month=ln.month, amount=ln.amount, project_id=ln.project_id,
            opportunity_id=ln.opportunity_id, reference=ln.reference, customer=ln.customer,
            name=ln.name, practice=ln.practice, region=ln.region, industry=ln.industry,
            category=ln.category, status=ln.status, owner_codes=ln.owner_codes, stage=ln.stage,
            probability=ln.probability,
        )
        for ln in collected.lines
    ]  # fmt: skip
    month_rows = [
        ForecastSnapshotMonth(month=m, committed=c, weighted=w, total=c + w)
        for m, (c, w) in collected.months.items()
    ]
    snapshot.checksum = compute_checksum(snapshot, line_rows, month_rows)
    session.add(snapshot)
    await session.flush()
    for row in line_rows:
        row.snapshot_id = snapshot.id
    for month_row in month_rows:
        month_row.snapshot_id = snapshot.id
    session.add_all(line_rows)
    session.add_all(month_rows)
    await session.flush()
    return snapshot


# ---- reading ---------------------------------------------------------------------------------


async def get_snapshot(session: AsyncSession, snapshot_id: int) -> ForecastSnapshot | None:
    return await session.get(ForecastSnapshot, snapshot_id)


async def snapshot_lines(session: AsyncSession, snapshot_id: int) -> list[ForecastSnapshotLine]:
    return list(
        await session.scalars(
            select(ForecastSnapshotLine)
            .where(ForecastSnapshotLine.snapshot_id == snapshot_id)
            .order_by(
                ForecastSnapshotLine.line_type,
                ForecastSnapshotLine.reference,
                ForecastSnapshotLine.month,
            )
        )
    )


async def load_lines(session: AsyncSession, snapshot_id: int) -> list[Line]:
    """The stored lines as plain `Line` values, the same type a live forecast produces."""
    return [
        Line(
            row.line_type, row.month, row.amount, row.reference, row.name, row.customer,
            project_id=row.project_id, opportunity_id=row.opportunity_id,
            practice=row.practice, region=row.region, industry=row.industry,
            category=row.category, status=row.status, owner_codes=list(row.owner_codes),
            stage=row.stage, probability=row.probability,
        )
        for row in await snapshot_lines(session, snapshot_id)
    ]  # fmt: skip


async def snapshot_months(session: AsyncSession, snapshot_id: int) -> list[ForecastSnapshotMonth]:
    return list(
        await session.scalars(
            select(ForecastSnapshotMonth)
            .where(ForecastSnapshotMonth.snapshot_id == snapshot_id)
            .order_by(ForecastSnapshotMonth.month)
        )
    )


async def verify_snapshot(session: AsyncSession, snapshot: ForecastSnapshot) -> bool:
    """Recompute the checksum from the stored rows: True when nothing has been altered."""
    lines = await snapshot_lines(session, snapshot.id)
    months = await snapshot_months(session, snapshot.id)
    return compute_checksum(snapshot, lines, months) == snapshot.checksum


def line_matches(line: Line, f: DealFilters) -> bool:
    """The PRD's forecast filters applied to a stored line (same rules as the live forecast)."""
    if f.owner and f.owner not in line.owner_codes:
        return False
    if f.practice and line.practice != f.practice:
        return False
    if f.region and line.region != f.region:
        return False
    if f.industry and line.industry != f.industry:
        return False
    if f.category and line.category != f.category:
        return False
    if f.customer and (line.customer or "").lower() != f.customer.strip().lower():
        return False
    if f.q:
        needle = f.q.strip().lower()
        haystack = (line.name, line.customer or "", line.reference)
        if not any(needle in value.lower() for value in haystack):
            return False
    return True


@dataclass
class MonthTotal:
    month: date
    committed: Decimal
    weighted: Decimal

    @property
    def total(self) -> Decimal:
        return self.committed + self.weighted


async def filtered_months(
    session: AsyncSession, snapshot: ForecastSnapshot, filters: DealFilters
) -> list[MonthTotal]:
    """Monthly totals of a snapshot. Unfiltered, these are the stored totals; filtered, they are
    summed from the stored lines (and equal the stored totals when nothing is excluded)."""
    stored = await snapshot_months(session, snapshot.id)
    if not filters.active():
        return [MonthTotal(m.month, m.committed, m.weighted) for m in stored]
    committed: dict[date, Decimal] = defaultdict(lambda: ZERO)
    weighted: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for line in await load_lines(session, snapshot.id):
        if not line_matches(line, filters):
            continue
        target = committed if line.line_type == "committed" else weighted
        target[line.month] += line.amount
    return [
        MonthTotal(m.month, committed.get(m.month, ZERO), weighted.get(m.month, ZERO))
        for m in stored
    ]
