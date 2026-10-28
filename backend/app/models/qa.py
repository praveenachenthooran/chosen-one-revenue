from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt


class QaRun(Base):
    """One execution of the deterministic QA checks (on demand or the nightly job)."""

    __tablename__ = "qa_runs"
    __table_args__ = (
        CheckConstraint("trigger IN ('manual', 'scheduled')", name="trigger_valid"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    triggered_by_label: Mapped[str] = mapped_column(String(100))
    as_of: Mapped[date | None] = mapped_column(Date)
    findings_new: Mapped[int] = mapped_column(Integer, server_default="0")
    findings_seen: Mapped[int] = mapped_column(Integer, server_default="0")
    findings_reopened: Mapped[int] = mapped_column(Integer, server_default="0")
    findings_resolved: Mapped[int] = mapped_column(Integer, server_default="0")
    findings_open: Mapped[int] = mapped_column(Integer, server_default="0")
    rule_counts: Mapped[dict[str, int]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class QaFindingEvent(Base):
    """Append-only audit trail of everything that happens to a finding."""

    __tablename__ = "qa_finding_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("qa_findings.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("qa_runs.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[CreatedAt]


class Investigation(Base):
    """An AI Revenue Detective (or deterministic fallback) investigation of one finding.

    Read-only by construction: it records what was looked at and concluded, and never changes
    revenue or the finding. `transcript` keeps the prompts and outputs (PRD: logged).
    """

    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint("status IN ('investigating', 'completed', 'failed')", name="status_valid"),
        CheckConstraint("mode IN ('ai', 'deterministic')", name="mode_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("qa_findings.id"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'investigating'"))
    mode: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    requested_by_label: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
