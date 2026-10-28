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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money
from app.models.projects import Project
from app.models.users import User

MONTH_START = "EXTRACT(DAY FROM month) = 1"


class ProjectMonthlyRevenue(Base):
    """Committed forecast revenue for one project in one calendar month.

    A blank workbook cell is stored as no row (unknown), never as zero.
    """

    __tablename__ = "project_monthly_revenue"
    __table_args__ = (
        UniqueConstraint("project_id", "month"),
        CheckConstraint(MONTH_START, name="month_is_first_day"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    month: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)  # the current forecast; editable in the app
    # The value the Excel import loaded for this month. Edits never change it, so the original
    # import stays reproducible. NULL means the month was added in the app (blank in the source).
    baseline_amount: Mapped[Decimal | None] = mapped_column(Money)
    source_cell: Mapped[str | None] = mapped_column(String(32))  # e.g. Forecast!I3
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship()


class RevenueRevision(Base):
    """Append-only audit event: who changed what, when, from which value to which."""

    __tablename__ = "revenue_revisions"
    __table_args__ = (
        CheckConstraint("change_type IN ('create', 'update', 'delete')", name="change_type_valid"),
        Index("ix_revenue_revisions_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    month: Mapped[date | None] = mapped_column(Date)
    field: Mapped[str] = mapped_column(String(64))
    change_type: Mapped[str] = mapped_column(String(16))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(100))  # e.g. "excel-import"
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    import_run_id: Mapped[int | None] = mapped_column(ForeignKey("import_runs.id"))
    created_at: Mapped[CreatedAt]

    actor: Mapped[User | None] = relationship()


class QaFinding(Base):
    """A QA finding awaiting a human decision (the agent and the engine never edit data).

    One row per fingerprint, ever: repeated runs update `last_seen` instead of adding rows, and a
    finding the engine no longer detects is marked `resolved` (and reopened if it returns).
    """

    __tablename__ = "qa_findings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'accepted', 'dismissed', 'resolved')", name="status_valid"
        ),
        CheckConstraint(
            "status IN ('open', 'resolved') "
            "OR (reviewed_by_id IS NOT NULL AND review_reason IS NOT NULL)",
            name="review_recorded",
        ),
        CheckConstraint("severity IN ('error', 'warning', 'info')", name="severity_valid"),
        UniqueConstraint("fingerprint"),
        Index("ix_qa_findings_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_opportunities.id"))
    related_project_ids: Mapped[list[int]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    rule_code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), server_default=text("'warning'"))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    # Deterministic estimate of the forecast revenue involved (see docs/milestone-5-notes.md).
    financial_impact: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'open'"))
    routed_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    first_run_id: Mapped[int | None] = mapped_column(ForeignKey("qa_runs.id"))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("qa_runs.id"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class OwnerAttestation(Base):
    """An owner confirming their rows were reviewed in a given attestation month."""

    __tablename__ = "owner_attestations"
    __table_args__ = (
        UniqueConstraint("project_id", "period"),
        CheckConstraint("EXTRACT(DAY FROM period) = 1", name="period_is_first_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    attested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    period: Mapped[date] = mapped_column(Date)
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
