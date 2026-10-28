"""Snapshots, exports, actuals and the accuracy report.

Reading is open to every signed-in user for snapshots and exports (the same people who can read
the live forecast). Taking a snapshot and loading actuals need Finance or an Administrator.
Actuals, their uploads and the accuracy report are visible to Finance, Sales leadership and
Administrators. Everything is enforced here, with CSRF on every change.
"""

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pipeline import Filters
from app.auth.deps import CurrentUser, require_csrf
from app.auth.permissions import role_names
from app.auth.roles import ADMIN, FINANCE, HISTORY_ANY_ROLES
from app.database import get_db_session
from app.fiscal import fiscal_period, fiscal_year
from app.models import (
    ActualsUpload,
    ExportEvent,
    ForecastSnapshot,
    JobRun,
    User,
)
from app.pipeline.forecast import DealFilters
from app.pipeline_schemas import ForecastMonth, ForecastYear, Unphased
from app.reporting_schemas import (
    AccuracyOut,
    ActualMonthOut,
    JobRunOut,
    SnapshotCreate,
    SnapshotList,
    SnapshotOut,
    SnapshotTotals,
    UploadDetail,
    UploadOut,
    UploadResultOut,
    VerifyOut,
)
from app.snapshots import actuals as actuals_module
from app.snapshots.accuracy import accuracy_report, actual_totals
from app.snapshots.calendar import first_business_day, month_start
from app.snapshots.exports import (
    CSV_TYPE,
    CSV_VIEWS,
    XLSX_TYPE,
    ExportData,
    filters_dict,
    to_csv,
    to_xlsx,
)
from app.snapshots.jobs import JOB_NAME
from app.snapshots.service import (
    MANUAL,
    SCHEDULED,
    Line,
    MonthTotal,
    collect_forecast,
    filtered_months,
    get_snapshot,
    line_matches,
    load_lines,
    take_snapshot,
    verify_snapshot,
)

router = APIRouter(tags=["reporting"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
ZERO = Decimal("0.00")
SNAPSHOT_ROLES = frozenset({FINANCE, ADMIN})  # may take a snapshot and load actuals
ACTUALS_ROLES = HISTORY_ANY_ROLES  # Finance, Sales leadership, Administrator may see actuals


def _error(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _require(user: User, roles: frozenset[str], what: str) -> None:
    if not role_names(user) & roles:
        raise _error(
            403,
            "forbidden_role",
            f"{what} is limited to " + ", ".join(sorted(roles)) + ".",
        )


# ---- snapshots -------------------------------------------------------------------------------


async def _snapshot_or_404(session: AsyncSession, snapshot_id: int) -> ForecastSnapshot:
    snapshot = await get_snapshot(session, snapshot_id)
    if snapshot is None:
        raise _error(404, "snapshot_not_found", "Snapshot not found.")
    return snapshot


@router.get("/snapshots", response_model=SnapshotList)
async def list_snapshots(session: Session, user: CurrentUser) -> SnapshotList:
    rows = list(
        await session.scalars(
            select(ForecastSnapshot).order_by(
                ForecastSnapshot.taken_at.desc(), ForecastSnapshot.id.desc()
            )
        )
    )
    today = datetime.now(UTC).date()
    period = month_start(today)
    taken = any(s.kind == SCHEDULED and s.period_month == period for s in rows)
    due_month = (
        date(period.year + (period.month == 12), period.month % 12 + 1, 1) if taken else period
    )
    last_job = await session.scalar(
        select(JobRun).where(JobRun.job == JOB_NAME).order_by(JobRun.id.desc()).limit(1)
    )
    return SnapshotList(
        items=[SnapshotOut.model_validate(s) for s in rows],
        next_scheduled=first_business_day(due_month.year, due_month.month),
        scheduled_taken_this_month=taken,
        last_job=JobRunOut.model_validate(last_job) if last_job else None,
        can_take=bool(role_names(user) & SNAPSHOT_ROLES),
    )


@router.post(
    "/snapshots", response_model=SnapshotOut, status_code=201, dependencies=[Depends(require_csrf)]
)
async def create_snapshot(
    body: SnapshotCreate, session: Session, user: CurrentUser
) -> ForecastSnapshot:
    """Take a snapshot now (Finance or Administrator). It can never be edited or deleted."""
    _require(user, SNAPSHOT_ROLES, "Taking a snapshot")
    snapshot = await take_snapshot(
        session,
        kind=MANUAL,
        now=datetime.now(UTC),
        actor=user,
        note=(body.note or "").strip() or None,
    )
    await session.commit()
    return snapshot


def _totals(snapshot: ForecastSnapshot, months: list[MonthTotal], filtered: bool) -> SnapshotTotals:
    rows = [
        ForecastMonth(
            month=m.month, fiscal_year=fiscal_year(m.month), fiscal_period=fiscal_period(m.month),
            committed=m.committed, weighted=m.weighted, total=m.total,
        )
        for m in months
    ]  # fmt: skip
    by_year: dict[str, list[ForecastMonth]] = defaultdict(list)
    for row in rows:
        by_year[row.fiscal_year].append(row)
    committed = sum((r.committed for r in rows), ZERO)
    weighted = sum((r.weighted for r in rows), ZERO)
    return SnapshotTotals(
        snapshot=SnapshotOut.model_validate(snapshot),
        months=rows,
        fiscal_years=[
            ForecastYear(
                fiscal_year=year,
                committed=sum((r.committed for r in group), ZERO),
                weighted=sum((r.weighted for r in group), ZERO),
                total=sum((r.total for r in group), ZERO),
            )
            for year, group in sorted(by_year.items())
        ],
        committed_total=committed,
        weighted_total=weighted,
        combined_total=committed + weighted,
        unphased=None
        if filtered
        else Unphased(deals=snapshot.unphased_deals, weighted_amount=snapshot.unphased_weighted),
        outside_window_weighted=None if filtered else snapshot.outside_window_weighted,
        filters_applied=filtered,
    )


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotTotals)
async def get_snapshot_totals(
    snapshot_id: int, session: Session, _: CurrentUser, filters: Filters
) -> SnapshotTotals:
    """A snapshot's monthly committed, weighted and combined totals, with the PRD filters
    applied to what was stored at the time."""
    snapshot = await _snapshot_or_404(session, snapshot_id)
    return _totals(snapshot, await filtered_months(session, snapshot, filters), filters.active())


@router.get("/snapshots/{snapshot_id}/verify", response_model=VerifyOut)
async def verify(snapshot_id: int, session: Session, _: CurrentUser) -> VerifyOut:
    """Recompute the snapshot's checksum from its stored rows."""
    snapshot = await _snapshot_or_404(session, snapshot_id)
    return VerifyOut(
        snapshot_id=snapshot.id,
        intact=await verify_snapshot(session, snapshot),
        checksum=snapshot.checksum,
    )


# ---- exports ---------------------------------------------------------------------------------


async def _send(
    session: AsyncSession,
    user: User,
    data: ExportData,
    *,
    source: Literal["current", "snapshot"],
    snapshot_id: int | None,
    file_format: Literal["xlsx", "csv"],
    view: str,
    stem: str,
) -> Response:
    if file_format == "csv" and view not in CSV_VIEWS:
        raise _error(422, "invalid_view", "view must be one of: " + ", ".join(CSV_VIEWS) + ".")
    body = to_xlsx(data) if file_format == "xlsx" else to_csv(data, view)
    session.add(
        ExportEvent(
            source=source, snapshot_id=snapshot_id, file_format=file_format,
            filters=filters_dict(data.filters), exported_by_id=user.id,
            exported_at=data.exported_at,
        )
    )  # fmt: skip
    await session.commit()
    suffix = "" if file_format == "xlsx" else f"-{view}"
    return Response(
        content=body,
        media_type=XLSX_TYPE if file_format == "xlsx" else CSV_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{stem}{suffix}.{file_format}"',
            "Cache-Control": "no-store",
        },
    )


def _filtered(lines: list[Line], filters: DealFilters) -> list[Line]:
    return [ln for ln in lines if line_matches(ln, filters)]


def _months_from(lines: list[Line], months: list[date]) -> list[MonthTotal]:
    committed: dict[date, Decimal] = defaultdict(lambda: ZERO)
    weighted: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for ln in lines:
        (committed if ln.line_type == "committed" else weighted)[ln.month] += ln.amount
    return [MonthTotal(m, committed.get(m, ZERO), weighted.get(m, ZERO)) for m in months]


@router.get("/forecast/export")
async def export_current(
    session: Session,
    user: CurrentUser,
    filters: Filters,
    file_format: Annotated[Literal["xlsx", "csv"], Query(alias="format")] = "xlsx",
    view: str = "monthly",
) -> Response:
    """The current forecast (committed, weighted, combined) for the chosen filters, as a file."""
    now = datetime.now(UTC)
    collected = await collect_forecast(session)
    lines = _filtered(collected.lines, filters)
    meta = [("Data as of", collected.as_of.isoformat() if collected.as_of else "not recorded")]
    data = ExportData(
        title="Current forecast",
        meta=meta,
        months=_months_from(lines, list(collected.months)),
        lines=lines,
        filters=filters,
        exported_at=now,
        exported_by=user.name,
    )
    return await _send(
        session, user, data, source="current", snapshot_id=None, file_format=file_format,
        view=view, stem=f"chosen-one-forecast-{now:%Y%m%dT%H%MZ}",
    )  # fmt: skip


@router.get("/snapshots/{snapshot_id}/export")
async def export_snapshot(
    snapshot_id: int,
    session: Session,
    user: CurrentUser,
    filters: Filters,
    file_format: Annotated[Literal["xlsx", "csv"], Query(alias="format")] = "xlsx",
    view: str = "monthly",
) -> Response:
    """A saved snapshot as a file, optionally filtered. The figures are the stored ones."""
    snapshot = await _snapshot_or_404(session, snapshot_id)
    lines = _filtered(await load_lines(session, snapshot.id), filters)
    meta = [
        ("Snapshot", f"#{snapshot.id} {snapshot.label}"),
        ("Snapshot kind", snapshot.kind),
        ("Snapshot taken (UTC)", snapshot.taken_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")),
        ("Snapshot taken by", snapshot.taken_by_label),
        ("Data as of", snapshot.as_of.isoformat() if snapshot.as_of else "not recorded"),
        ("Snapshot checksum (SHA-256)", snapshot.checksum),
    ]
    data = ExportData(
        title=f"Forecast snapshot #{snapshot.id}",
        meta=meta,
        months=await filtered_months(session, snapshot, filters),
        lines=lines,
        filters=filters,
        exported_at=datetime.now(UTC),
        exported_by=user.name,
    )
    return await _send(
        session, user, data, source="snapshot", snapshot_id=snapshot.id, file_format=file_format,
        view=view, stem=f"chosen-one-snapshot-{snapshot.id}-{snapshot.period_month:%Y-%m}",
    )  # fmt: skip


# ---- actuals ---------------------------------------------------------------------------------


async def _read_body(request: Request) -> bytes:
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > actuals_module.MAX_BYTES:
            raise _error(413, "file_too_large", "The file is larger than 5 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/actuals/uploads", response_model=UploadResultOut, dependencies=[Depends(require_csrf)]
)
async def upload_actuals(
    request: Request,
    session: Session,
    user: CurrentUser,
    filename: Annotated[str, Query(min_length=1, max_length=300, description="The file's name")],
    dry_run: Annotated[bool, Query(description="Validate and reconcile without storing")] = False,
) -> UploadResultOut:
    """Load monthly actuals from a CSV or XLSX file sent as the request body.

    All or nothing: any invalid row rejects the whole file (422 with every problem listed).
    `dry_run=true` returns the same reconciliation without storing anything.
    """
    _require(user, SNAPSHOT_ROLES, "Loading actuals")
    content = await _read_body(request)
    try:
        result = await actuals_module.process_upload(
            session, filename=filename, content=content, actor=user, now=datetime.now(UTC),
            dry_run=dry_run,
        )  # fmt: skip
    except actuals_module.ActualsFileError as exc:
        raise _error(exc.status, exc.code, exc.message) from exc
    if result.status == "rejected" and not dry_run:
        await session.commit()  # keep the rejected attempt for the audit trail
        raise _error(
            422,
            "actuals_rejected",
            f"Nothing was loaded: {result.report['rows_rejected']} row(s) have problems. "
            "Fix them and upload again.",
            report=result.report,
            upload_id=result.upload.id if result.upload else None,
        )
    await session.commit()
    return UploadResultOut(
        dry_run=dry_run,
        status=result.status,
        upload_id=result.upload.id if result.upload else None,
        report=result.report,
    )


@router.get("/actuals/uploads", response_model=list[UploadOut])
async def list_uploads(session: Session, user: CurrentUser) -> list[ActualsUpload]:
    _require(user, ACTUALS_ROLES, "Actuals")
    return list(
        await session.scalars(select(ActualsUpload).order_by(ActualsUpload.id.desc()).limit(200))
    )


@router.get("/actuals/uploads/{upload_id}", response_model=UploadDetail)
async def get_upload(upload_id: int, session: Session, user: CurrentUser) -> ActualsUpload:
    _require(user, ACTUALS_ROLES, "Actuals")
    upload = await session.get(ActualsUpload, upload_id)
    if upload is None:
        raise _error(404, "upload_not_found", "Upload not found.")
    return upload


@router.get("/actuals/months", response_model=list[ActualMonthOut])
async def actual_months(session: Session, user: CurrentUser) -> list[ActualMonthOut]:
    """The actuals now on record by month, beside the committed forecast for the same month."""
    _require(user, ACTUALS_ROLES, "Actuals")
    totals = await actual_totals(session)
    forecast = await actuals_module.forecast_now(session, list(totals))
    return [
        ActualMonthOut(
            month=month, fiscal_year=fiscal_year(month), actual_total=total,
            committed_forecast=forecast.get(month, ZERO),
        )
        for month, total in totals.items()
    ]  # fmt: skip


# ---- accuracy --------------------------------------------------------------------------------


@router.get("/accuracy", response_model=AccuracyOut)
async def accuracy(session: Session, user: CurrentUser) -> AccuracyOut:
    """Variance of the M-1, M-2 and M-3 snapshots against actuals, by month and in total, with
    months outside the +/-5% target flagged."""
    _require(user, ACTUALS_ROLES, "The accuracy report")
    return AccuracyOut.model_validate(await accuracy_report(session))
