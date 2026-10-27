from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt


class HubspotStage(Base):
    """A HubSpot pipeline stage with the probability used to weight deals in it.

    `in_forecast` is the configured forecast-stage rule: open stages with it set contribute
    weighted pipeline; earlier stages are excluded (PRD: deals below the forecast stage).
    """

    __tablename__ = "hubspot_stages"
    __table_args__ = (
        CheckConstraint("probability BETWEEN 0 AND 1", name="prob_range"),
        CheckConstraint("kind IN ('open', 'won', 'lost')", name="kind_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    probability: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    kind: Mapped[str] = mapped_column(String(8))
    in_forecast: Mapped[bool] = mapped_column(Boolean)
    position: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HubspotSyncRun(Base):
    __tablename__ = "hubspot_sync_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    deals_seen: Mapped[int] = mapped_column(Integer, server_default="0")
    deals_created: Mapped[int] = mapped_column(Integer, server_default="0")
    deals_updated: Mapped[int] = mapped_column(Integer, server_default="0")
    deals_unchanged: Mapped[int] = mapped_column(Integer, server_default="0")
    drafts_created: Mapped[int] = mapped_column(Integer, server_default="0")
    lines_retired: Mapped[int] = mapped_column(Integer, server_default="0")
    findings_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    triggered_by_label: Mapped[str] = mapped_column(String(100), server_default=text("'system'"))
    created_at: Mapped[CreatedAt]


class HubspotSyncFinding(Base):
    """A data-quality finding raised while syncing (never silently fixed)."""

    __tablename__ = "hubspot_sync_findings"
    __table_args__ = (
        CheckConstraint("severity IN ('error', 'warning', 'info')", name="sev_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("hubspot_sync_runs.id", ondelete="CASCADE"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_opportunities.id"))
    hubspot_deal_id: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class DraftConfirmation(Base):
    """The request for an owner to confirm the draft project created from a Closed Won deal."""

    __tablename__ = "draft_confirmations"
    __table_args__ = (UniqueConstraint("project_id"), UniqueConstraint("opportunity_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("hubspot_opportunities.id"))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Existing projects for the same customer, shown to the confirmer (PRD: possible duplicate).
    possible_duplicates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    duplicates_acknowledged: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[CreatedAt]


class PipelineEvent(Base):
    """Append-only log of pipeline lifecycle changes (who or what, when, from and to)."""

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_opportunities.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("hubspot_sync_runs.id"))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(100))
    from_value: Mapped[str | None] = mapped_column(Text)
    to_value: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[CreatedAt]
