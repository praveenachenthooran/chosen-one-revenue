from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, Money

MONTH_START = "EXTRACT(DAY FROM month) = 1"


class ForecastSnapshot(Base):
    """An immutable point-in-time copy of the whole forecast (committed and weighted pipeline).

    The database rejects every UPDATE and DELETE on this table and its lines and months, so a
    snapshot can never change after it is taken. Filters are applied when a snapshot is *read*;
    the snapshot itself always holds the unfiltered company forecast so any filtered view of it
    can be reproduced later.
    """

    __tablename__ = "forecast_snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('scheduled', 'manual')", name="kind_valid"),
        CheckConstraint("EXTRACT(DAY FROM period_month) = 1", name="period_is_first_day"),
        # At most one scheduled snapshot per calendar month: what makes the monthly job idempotent.
        Index(
            "uq_forecast_snapshots_scheduled_period",
            "period_month",
            unique=True,
            postgresql_where=text("kind = 'scheduled'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    period_month: Mapped[date] = mapped_column(Date)  # the calendar month it was taken in
    kind: Mapped[str] = mapped_column(String(16))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    taken_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    taken_by_label: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    # What the snapshot was built from, so a historical report can be explained and reproduced.
    as_of: Mapped[date | None] = mapped_column(Date)
    import_run_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_sync_runs.id"))
    stage_probabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer)
    committed_total: Mapped[Decimal] = mapped_column(Money)
    weighted_total: Mapped[Decimal] = mapped_column(Money)
    combined_total: Mapped[Decimal] = mapped_column(Money)
    unphased_deals: Mapped[int] = mapped_column(Integer)
    unphased_weighted: Mapped[Decimal] = mapped_column(Money)
    outside_window_weighted: Mapped[Decimal] = mapped_column(Money)
    project_count: Mapped[int] = mapped_column(Integer)
    deal_count: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))  # SHA-256 of the canonical content
    created_at: Mapped[CreatedAt]


class ForecastSnapshotLine(Base):
    """One committed project-month or one weighted deal-month, with the attributes it had then.

    The attributes are copied, not joined, so later renames, owner changes or re-categorisation
    never alter what a historical snapshot reports.
    """

    __tablename__ = "forecast_snapshot_lines"
    __table_args__ = (
        CheckConstraint("line_type IN ('committed', 'weighted')", name="type_valid"),
        CheckConstraint(MONTH_START, name="month_is_first_day"),
        Index("ix_forecast_snapshot_lines_snapshot_id_month", "snapshot_id", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id"))
    line_type: Mapped[str] = mapped_column(String(10))
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_opportunities.id"))
    reference: Mapped[str] = mapped_column(String(64))  # Row ID (committed) or HubSpot Deal ID
    customer: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    practice: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(32))
    industry: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    owner_codes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    stage: Mapped[str | None] = mapped_column(String(100))
    probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))


class ForecastSnapshotMonth(Base):
    """The stored monthly totals of a snapshot (the numbers Finance and the CRO see)."""

    __tablename__ = "forecast_snapshot_months"
    __table_args__ = (CheckConstraint(MONTH_START, name="month_is_first_day"),)

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    committed: Mapped[Decimal] = mapped_column(Money)
    weighted: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)


class ActualsUpload(Base):
    """One attempt to load monthly actuals from a CSV or XLSX file, kept for reconciliation."""

    __tablename__ = "actuals_uploads"
    __table_args__ = (CheckConstraint("status IN ('completed', 'rejected')", name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_by_label: Mapped[str] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rows_total: Mapped[int] = mapped_column(Integer)
    rows_accepted: Mapped[int] = mapped_column(Integer)
    rows_rejected: Mapped[int] = mapped_column(Integer)
    rows_unchanged: Mapped[int] = mapped_column(Integer)
    rows_replaced: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    months: Mapped[list[str]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the reconciliation report as shown
    created_at: Mapped[CreatedAt]


class MonthlyActual(Base):
    """Finance-supplied actual revenue for a customer, optionally narrowed to one project.

    A later upload for the same key supersedes the earlier value rather than overwriting it, so
    the earlier figure stays on record.
    """

    __tablename__ = "monthly_actuals"
    __table_args__ = (
        CheckConstraint(MONTH_START, name="month_is_first_day"),
        Index(
            "uq_monthly_actuals_active_project",
            "project_id",
            "month",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND project_id IS NOT NULL"),
        ),
        Index(
            "uq_monthly_actuals_active_customer",
            "customer_id",
            "month",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND project_id IS NULL"),
        ),
        Index("ix_monthly_actuals_month", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("actuals_uploads.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_upload_id: Mapped[int | None] = mapped_column(ForeignKey("actuals_uploads.id"))
    created_at: Mapped[CreatedAt]


class JobRun(Base):
    """The outcome of every scheduled-job invocation, including the ones that did nothing."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'skipped', 'failed')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_snapshots.id"))


class ExportEvent(Base):
    """Append-only record of every spreadsheet export: who, what, with which filters."""

    __tablename__ = "export_events"
    __table_args__ = (CheckConstraint("source IN ('current', 'snapshot')", name="source_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_snapshots.id"))
    file_format: Mapped[str] = mapped_column(String(8))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    exported_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
