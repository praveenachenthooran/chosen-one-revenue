from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt
from app.models.users import User


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[CreatedAt]

    projects: Mapped[list[Project]] = relationship(back_populates="customer")


class Category(Base):
    """Predefined revenue category codes (EE / EN / NN)."""

    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("customer_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Original workbook "Row ID" (e.g. R1001); the stable key for repeat imports.
    source_row_id: Mapped[str] = mapped_column(String(32), unique=True)
    source_row_number: Mapped[int | None]
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    name: Mapped[str] = mapped_column(String(300))
    practice: Mapped[str | None] = mapped_column(String(64))  # the sheet's TECH column
    region: Mapped[str | None] = mapped_column(String(32))
    industry: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    billing_type: Mapped[str | None] = mapped_column(String(64))
    category_code: Mapped[str | None] = mapped_column(ForeignKey("categories.code"))
    status: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))  # not present in the workbook
    source_last_updated: Mapped[date | None] = mapped_column(Date)
    source_last_updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="projects")
    owners: Mapped[list[ProjectOwner]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectOwner.role"
    )


class ProjectOwner(Base):
    """One accountable person per ownership role per project."""

    __tablename__ = "project_owners"
    __table_args__ = (
        UniqueConstraint("project_id", "role"),
        CheckConstraint("role IN ('client_partner', 'ae', 'commercial_owner')", name="role_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(32))

    project: Mapped[Project] = relationship(back_populates="owners")
    user: Mapped[User] = relationship()
