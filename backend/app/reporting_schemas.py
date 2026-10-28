"""Response and request models for snapshots, actuals and accuracy (money as decimal strings)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.pipeline_schemas import ForecastMonth, ForecastYear, Unphased


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SnapshotOut(Out):
    id: int
    label: str
    period_month: date
    kind: str
    taken_at: datetime
    taken_by_label: str
    note: str | None
    as_of: date | None
    import_run_id: int | None
    sync_run_id: int | None
    stage_probabilities: list[dict[str, Any]]
    committed_total: Decimal
    weighted_total: Decimal
    combined_total: Decimal
    unphased_deals: int
    unphased_weighted: Decimal
    outside_window_weighted: Decimal
    project_count: int
    deal_count: int
    line_count: int
    checksum: str


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class JobRunOut(Out):
    id: int
    job: str
    started_at: datetime
    finished_at: datetime
    status: str
    message: str
    snapshot_id: int | None


class SnapshotList(Out):
    items: list[SnapshotOut]
    next_scheduled: date  # the first business day of the next month still to be snapshotted
    scheduled_taken_this_month: bool
    last_job: JobRunOut | None
    can_take: bool


class SnapshotTotals(Out):
    """A snapshot's monthly totals, optionally filtered. Deals that could not be phased have no
    month, so they are reported only for the unfiltered snapshot."""

    snapshot: SnapshotOut
    months: list[ForecastMonth]
    fiscal_years: list[ForecastYear]
    committed_total: Decimal
    weighted_total: Decimal
    combined_total: Decimal
    unphased: Unphased | None
    outside_window_weighted: Decimal | None
    filters_applied: bool


class VerifyOut(Out):
    snapshot_id: int
    intact: bool
    checksum: str


class UploadOut(Out):
    id: int
    filename: str
    status: str
    uploaded_at: datetime
    uploaded_by_label: str
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    rows_unchanged: int
    rows_replaced: int
    total_amount: Decimal
    months: list[str]


class UploadDetail(UploadOut):
    report: dict[str, Any]


class UploadResultOut(Out):
    """The outcome of loading (or previewing) an actuals file, with its reconciliation report."""

    dry_run: bool
    status: str
    upload_id: int | None
    report: dict[str, Any]


class ActualMonthOut(Out):
    month: date
    fiscal_year: str
    actual_total: Decimal
    committed_forecast: Decimal


class AccuracyLagOut(Out):
    lag: int
    status: str
    snapshot_id: int | None
    snapshot_label: str | None
    snapshot_taken_at: datetime | None
    forecast: Decimal | None
    committed: Decimal | None
    weighted: Decimal | None
    variance: Decimal | None
    variance_percent: Decimal | None
    flagged: bool


class AccuracyMonthOut(Out):
    month: date
    fiscal_year: str
    actual: Decimal
    lags: list[AccuracyLagOut]


class AccuracyTotalOut(Out):
    lag: int
    months_compared: int
    months_flagged: int
    forecast: Decimal
    actual: Decimal
    variance: Decimal
    variance_percent: Decimal | None
    flagged: bool


class AccuracyOut(Out):
    tolerance_percent: Decimal
    target_lag: int
    months: list[AccuracyMonthOut]
    totals: list[AccuracyTotalOut]
