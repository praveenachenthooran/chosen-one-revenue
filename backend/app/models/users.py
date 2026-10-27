from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class User(Base):
    """A person referenced by the forecast.

    Rows imported from the workbook are *not* authorized application users:
    `is_provisioned` stays false until an administrator provisions the person
    against a verified Google Workspace identity (Milestone 2).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    location: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(String(500))
    is_provisioned: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Google's stable account identifier (the OIDC "sub" claim), bound on first sign-in. Once
    # set it is the authoritative identity; the email is only used for that first match.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, order_by=Role.name)
