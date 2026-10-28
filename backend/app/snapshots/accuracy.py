"""Forecast accuracy: what the forecast said, M-1, M-2 and M-3 months ahead, against actuals.

For an actuals month T, the "M-k snapshot" is the snapshot taken in month T-k (the month-start
snapshot: the scheduled one if there is one, otherwise the earliest manual one). The forecast it
holds for month T is the combined (committed + weighted) figure, the same number the forecast
screen shows. Variance is forecast minus actual; the percentage is relative to the actual. A month
is flagged when the forecast is outside the tolerance (PRD: +/-5%; the PRD's target is the M-3
look-ahead, M-1 and M-2 are shown on the same scale for comparison).

Nothing is estimated: a month with no snapshot for a look-ahead, or whose snapshot did not cover
that month, is shown as such and left out of the totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fiscal import fiscal_year
from app.models import ForecastSnapshot, ForecastSnapshotMonth, MonthlyActual
from app.pipeline.weighting import add_months

ZERO = Decimal("0.00")
TOLERANCE_PERCENT = Decimal("5")
LAGS = (1, 2, 3)
TARGET_LAG = 3


def percent(variance: Decimal, actual: Decimal) -> Decimal | None:
    """Variance as a percentage of the actual (2 decimals), or None when the actual is zero."""
    if actual == 0:
        return None
    return (variance / abs(actual) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_flagged(forecast: Decimal, actual: Decimal, tolerance_percent: Decimal) -> bool:
    """Outside +/-tolerance. Exactly on the limit is within it. A zero actual is flagged only
    if something was forecast, because a percentage cannot be taken of zero."""
    if actual == 0:
        return forecast != 0
    return abs(forecast - actual) * 100 > tolerance_percent * abs(actual)


@dataclass
class LagResult:
    lag: int
    status: str  # ok | no_snapshot | month_not_in_snapshot
    snapshot_id: int | None = None
    snapshot_label: str | None = None
    snapshot_taken_at: datetime | None = None
    forecast: Decimal | None = None
    committed: Decimal | None = None
    weighted: Decimal | None = None
    variance: Decimal | None = None
    variance_percent: Decimal | None = None
    flagged: bool = False


@dataclass
class MonthResult:
    month: date
    fiscal_year: str
    actual: Decimal
    lags: list[LagResult]


@dataclass
class LagTotal:
    lag: int
    months_compared: int = 0
    months_flagged: int = 0
    forecast: Decimal = ZERO
    actual: Decimal = ZERO
    variance: Decimal = ZERO
    variance_percent: Decimal | None = None
    flagged: bool = False


@dataclass
class AccuracyReport:
    tolerance_percent: Decimal
    target_lag: int
    months: list[MonthResult] = field(default_factory=lambda: [])
    totals: list[LagTotal] = field(default_factory=lambda: [])


async def actual_totals(session: AsyncSession) -> dict[date, Decimal]:
    """The current actuals (superseded values excluded) summed by month."""
    rows = await session.execute(
        select(MonthlyActual.month, func.sum(MonthlyActual.amount))
        .where(MonthlyActual.superseded_at.is_(None))
        .group_by(MonthlyActual.month)
        .order_by(MonthlyActual.month)
    )
    return {month: Decimal(total) for month, total in rows}


async def accuracy_report(
    session: AsyncSession, tolerance_percent: Decimal = TOLERANCE_PERCENT
) -> AccuracyReport:
    actuals = await actual_totals(session)
    report = AccuracyReport(tolerance_percent=tolerance_percent, target_lag=TARGET_LAG)
    totals = {lag: LagTotal(lag) for lag in LAGS}
    if not actuals:
        report.totals = list(totals.values())
        return report

    periods = {add_months(month, -lag) for month in actuals for lag in LAGS}
    # The month-start snapshot of each period: scheduled first, then the earliest taken.
    snapshots: dict[date, ForecastSnapshot] = {}
    for snapshot in await session.scalars(
        select(ForecastSnapshot)
        .where(ForecastSnapshot.period_month.in_(periods))
        .order_by(
            case((ForecastSnapshot.kind == "scheduled", 0), else_=1),
            ForecastSnapshot.taken_at,
            ForecastSnapshot.id,
        )
    ):
        snapshots.setdefault(snapshot.period_month, snapshot)
    stored: dict[tuple[int, date], ForecastSnapshotMonth] = {}
    if snapshots:
        for row in await session.scalars(
            select(ForecastSnapshotMonth).where(
                ForecastSnapshotMonth.snapshot_id.in_([s.id for s in snapshots.values()]),
                ForecastSnapshotMonth.month.in_(list(actuals)),
            )
        ):
            stored[(row.snapshot_id, row.month)] = row

    for month, actual in actuals.items():
        results: list[LagResult] = []
        for lag in LAGS:
            snapshot = snapshots.get(add_months(month, -lag))
            if snapshot is None:
                results.append(LagResult(lag, "no_snapshot"))
                continue
            row = stored.get((snapshot.id, month))
            if row is None:
                results.append(
                    LagResult(
                        lag, "month_not_in_snapshot", snapshot.id, snapshot.label, snapshot.taken_at
                    )
                )
                continue
            variance = row.total - actual
            flagged = is_flagged(row.total, actual, tolerance_percent)
            results.append(
                LagResult(
                    lag, "ok", snapshot.id, snapshot.label, snapshot.taken_at,
                    forecast=row.total, committed=row.committed, weighted=row.weighted,
                    variance=variance, variance_percent=percent(variance, actual), flagged=flagged,
                )
            )  # fmt: skip
            total = totals[lag]
            total.months_compared += 1
            total.months_flagged += int(flagged)
            total.forecast += row.total
            total.actual += actual
        report.months.append(MonthResult(month, fiscal_year(month), actual, results))

    for total in totals.values():
        total.variance = total.forecast - total.actual
        if total.months_compared:
            total.variance_percent = percent(total.variance, total.actual)
            total.flagged = is_flagged(total.forecast, total.actual, tolerance_percent)
    report.totals = list(totals.values())
    return report
