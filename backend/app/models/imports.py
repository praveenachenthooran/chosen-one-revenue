from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money


class ImportRun(Base):
    """One execution of the Excel migration, kept for traceability."""

    __tablename__ = "import_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file: Mapped[str] = mapped_column(String(500))
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_as_of: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    rows_read: Mapped[int] = mapped_column(Integer, server_default="0")
    projects_created: Mapped[int] = mapped_column(Integer, server_default="0")
    projects_updated: Mapped[int] = mapped_column(Integer, server_default="0")
    projects_unchanged: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(Integer, server_default="0")
    warnings: Mapped[int] = mapped_column(Integer, server_default="0")
    reconciled: Mapped[bool | None] = mapped_column(Boolean)
    month_columns: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]

    issues: Mapped[list[ImportIssue]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ImportIssue.id"
    )
    reconciliation: Mapped[list[ImportReconciliation]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ImportReconciliation.month"
    )


class ImportIssue(Base):
    """A validation error (row rejected) or warning (row imported, needs a look)."""

    __tablename__ = "import_issues"
    __table_args__ = (CheckConstraint("severity IN ('error', 'warning')", name="severity_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    sheet: Mapped[str] = mapped_column(String(64))
    row_number: Mapped[int | None]
    source_row_id: Mapped[str | None] = mapped_column(String(32))
    field: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    row_rejected: Mapped[bool] = mapped_column(Boolean, server_default="false")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    run: Mapped[ImportRun] = relationship(back_populates="issues")


class ImportReconciliation(Base):
    """Per-month comparison of workbook totals to database totals."""

    __tablename__ = "import_reconciliation"
    __table_args__ = (UniqueConstraint("run_id", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date)
    source_total: Mapped[Decimal] = mapped_column(Money)  # sum of accepted source cells
    database_total: Mapped[Decimal] = mapped_column(Money)
    excel_reported_total: Mapped[Decimal | None] = mapped_column(Money)  # cached Excel formula
    matches: Mapped[bool] = mapped_column(Boolean)

    run: Mapped[ImportRun] = relationship(back_populates="reconciliation")
