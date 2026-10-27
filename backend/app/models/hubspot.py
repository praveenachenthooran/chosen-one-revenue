from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt
from app.models.projects import Project


class HubspotOpportunity(Base):
    """A HubSpot deal as last synced. A read-only mirror: nothing is ever written to HubSpot."""

    __tablename__ = "hubspot_opportunities"
    __table_args__ = (
        CheckConstraint("probability IS NULL OR probability BETWEEN 0 AND 1", name="prob_range"),
        CheckConstraint(
            "lifecycle IN ('weighted', 'excluded', 'closed_won', 'closed_lost')",
            name="lifecycle_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hubspot_deal_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    company_name: Mapped[str | None] = mapped_column(String(200))
    company_hubspot_id: Mapped[str | None] = mapped_column(String(32))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(64))
    probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    # HubSpot amounts carry up to four decimals; they are stored exactly as exported.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_start: Mapped[date | None] = mapped_column(Date)
    duration_months: Mapped[int | None]
    practice: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Where the deal stands in the forecast, recomputed at every sync from its stage:
    #   weighted    open deal at/after the forecast stage: contributes a weighted pipeline line
    #   closed_won  won; contributes at 100% until its draft project is confirmed
    #   closed_lost / excluded (below the forecast stage, unknown stage, or gone from HubSpot)
    lifecycle: Mapped[str] = mapped_column(String(16), server_default=text("'excluded'"))
    lifecycle_reason: Mapped[str | None] = mapped_column(String(64))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # The company name, or None when HubSpot only supplied a numeric company ID.
    company_resolved_name: Mapped[str | None] = mapped_column(String(200))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    link: Mapped[DealProjectLink | None] = relationship(back_populates="opportunity")


class DealProjectLink(Base):
    """Links a deal to the committed project it funds.

    A deal links to at most one project, so it cannot be counted twice (FR-14).
    """

    __tablename__ = "deal_project_links"
    __table_args__ = (
        UniqueConstraint("opportunity_id"),
        CheckConstraint("link_source IN ('workbook_map', 'draft', 'manual')", name="source_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("hubspot_opportunities.id", ondelete="CASCADE")
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # workbook_map: an explicit Deal -> Row ID mapping from the source; draft: made by Closed Won
    link_source: Mapped[str] = mapped_column(String(24), server_default=text("'manual'"))
    created_at: Mapped[CreatedAt]

    opportunity: Mapped[HubspotOpportunity] = relationship(back_populates="link")
    project: Mapped[Project] = relationship()
