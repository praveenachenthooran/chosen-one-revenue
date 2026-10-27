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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money
from app.models.projects import Project


class HubspotOpportunity(Base):
    """A HubSpot deal. Table only in Milestone 1; there is no sync workflow yet."""

    __tablename__ = "hubspot_opportunities"
    __table_args__ = (
        CheckConstraint("probability IS NULL OR probability BETWEEN 0 AND 1", name="prob_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hubspot_deal_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    company_name: Mapped[str | None] = mapped_column(String(200))
    company_hubspot_id: Mapped[str | None] = mapped_column(String(32))
    owner_name: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(64))
    probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_start: Mapped[date | None] = mapped_column(Date)
    duration_months: Mapped[int | None]
    practice: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    link: Mapped[DealProjectLink | None] = relationship(back_populates="opportunity")


class DealProjectLink(Base):
    """Links a deal to the committed project it funds.

    A deal links to at most one project, so it cannot be counted twice (FR-14).
    """

    __tablename__ = "deal_project_links"
    __table_args__ = (UniqueConstraint("opportunity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("hubspot_opportunities.id", ondelete="CASCADE")
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_at: Mapped[CreatedAt]

    opportunity: Mapped[HubspotOpportunity] = relationship(back_populates="link")
    project: Mapped[Project] = relationship()
