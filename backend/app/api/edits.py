"""Monthly forecast editing and audit history.

Rules, in order: authenticate -> CSRF -> project exists -> permission -> validate -> concurrency
check -> write the value and its audit event in ONE transaction.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.queries import month_columns
from app.auth.deps import CurrentUser, require_csrf
from app.auth.permissions import can_view_history, edit_decision, owner_summary
from app.database import get_db_session
from app.models import Project, ProjectMonthlyRevenue, ProjectOwner, RevenueRevision, User
from app.schemas import HistoryItem, HistoryPage, RevenueEditRequest, RevenueEditResult

router = APIRouter(tags=["forecast"])
Session = Annotated[AsyncSession, Depends(get_db_session)]

AMOUNT_PATTERN = re.compile(
    r"[0-9]{1,12}(\.[0-9]{1,2})?"
)  # ASCII digits only (\d matches other scripts)
MAX_REASON_LENGTH = 500
IMPORT_ACTOR = "excel-import"


def _error(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def parse_amount(value: object, code: str = "invalid_amount") -> Decimal:
    """Accept only a plain, non-negative number with at most two decimals.

    Text, formulas such as "=90000-55000", exponent forms, NaN and negatives are all rejected;
    nothing is ever evaluated or coerced.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise _error(422, code, "Enter a number. Text and formulas are not accepted.")
    text = format(Decimal(repr(value)), "f") if isinstance(value, float) else str(value).strip()
    if text.startswith("-"):
        raise _error(422, "negative_amount", "Revenue cannot be negative.")
    if not AMOUNT_PATTERN.fullmatch(text):
        raise _error(
            422,
            code,
            "Enter a plain number with at most two decimal places (use 0 for no revenue). "
            "Text and formulas are not accepted.",
        )
    return Decimal(text).quantize(Decimal("0.01"))


def _parse_reason(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _error(422, "invalid_reason", "The reason must be text.")
    reason = value.strip()
    if len(reason) > MAX_REASON_LENGTH:
        raise _error(
            422, "invalid_reason", f"Keep the reason under {MAX_REASON_LENGTH} characters."
        )
    return reason or None


async def _locked_project(session: AsyncSession, project_id: int) -> Project:
    """Load the project and lock its row, so edits to one project are applied one at a time."""
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
        .with_for_update(of=Project)
    )
    if project is None:
        raise _error(404, "project_not_found", "Project not found.")
    return project


@router.put(
    "/projects/{project_id}/revenue/{month}",
    response_model=RevenueEditResult,
    dependencies=[Depends(require_csrf)],
)
async def edit_month(
    project_id: int,
    month: date,
    body: RevenueEditRequest,
    user: CurrentUser,
    session: Session,
) -> RevenueEditResult:
    """Set one month of a project's current forecast (the import baseline is never touched)."""
    project = await _locked_project(session, project_id)

    decision = edit_decision(user, project)
    if not decision.allowed:
        raise _error(
            403,
            decision.code or "forbidden",
            decision.message or "You cannot edit this project.",
            owners=owner_summary(project),
        )

    horizon = await month_columns(session)
    if month.day != 1:
        raise _error(
            422, "invalid_month", "Months must be the first day of a month, e.g. 2026-10-01."
        )
    if month not in horizon:
        span = f"{horizon[0]:%b %Y} - {horizon[-1]:%b %Y}" if horizon else "none imported"
        raise _error(422, "month_out_of_range", f"That month is outside the forecast ({span}).")

    new_amount = parse_amount(body.amount)
    expected = None if body.expected_amount is None else parse_amount(
        body.expected_amount, "invalid_expected_amount"
    )  # fmt: skip
    reason = _parse_reason(body.reason)

    current = await session.scalar(
        select(ProjectMonthlyRevenue).where(
            ProjectMonthlyRevenue.project_id == project_id, ProjectMonthlyRevenue.month == month
        )
    )
    current_amount = current.amount if current else None
    if current_amount != expected:
        raise _error(
            409,
            "conflict",
            "Someone else changed this value since you loaded it. Refresh to see the latest.",
            current_amount=None if current_amount is None else str(current_amount),
        )

    revision_id: int | None = None
    if current_amount != new_amount:
        if current is None:
            session.add(
                ProjectMonthlyRevenue(
                    project_id=project_id, month=month, amount=new_amount, baseline_amount=None
                )
            )
        else:
            current.amount = new_amount
        revision = RevenueRevision(
            project_id=project_id,
            month=month,
            field="revenue",
            change_type="create" if current is None else "update",
            old_value=None if current_amount is None else str(current_amount),
            new_value=str(new_amount),
            actor_user_id=user.id,
            actor_label=user.name,
            note=reason,
        )
        session.add(revision)
        await session.flush()
        revision_id = revision.id
        await session.commit()  # the value and its audit event become durable together

    total = await session.scalar(
        select(func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)).where(
            ProjectMonthlyRevenue.project_id == project_id
        )
    )
    return RevenueEditResult(
        project_id=project_id,
        month=month,
        amount=new_amount,
        previous_amount=current_amount,
        changed=revision_id is not None,
        project_total=Decimal(total or 0),
        revision_id=revision_id,
    )


@router.get("/projects/{project_id}/history", response_model=HistoryPage)
async def project_history(
    project_id: int,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryPage:
    """Audit events for one project, newest first. Read-only; there is no way to alter them."""
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
    )
    if project is None:
        raise _error(404, "project_not_found", "Project not found.")
    if not can_view_history(user, project):
        raise _error(
            403,
            "history_forbidden",
            "Change history is visible to this project's owners, Finance, Sales leadership "
            "and Administrators.",
            owners=owner_summary(project),
        )

    where = RevenueRevision.project_id == project_id
    total = await session.scalar(select(func.count()).select_from(RevenueRevision).where(where))
    rows = await session.execute(
        select(RevenueRevision, User.name)
        .outerjoin(User, User.id == RevenueRevision.actor_user_id)
        .where(where)
        .order_by(RevenueRevision.created_at.desc(), RevenueRevision.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return HistoryPage(
        items=[
            HistoryItem(
                id=r.id,
                created_at=r.created_at,
                field=r.field,
                month=r.month,
                change_type=r.change_type,
                old_value=r.old_value,
                new_value=r.new_value,
                reason=r.note,
                actor_name=name or r.actor_label,
                from_import=r.actor_user_id is None and r.actor_label == IMPORT_ACTOR,
            )
            for r, name in rows
        ],
        total_items=total or 0,
    )
