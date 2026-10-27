"""Closed Won -> draft committed project -> owner confirmation (PRD FR-09, FR-10, FR-14).

A Closed Won deal with no existing project gets a *draft* project linked to its Deal ID, with a
proposed schedule (amount at 100%, spread from the expected start). While the project is a draft
it is NOT part of committed totals and the deal still counts as weighted pipeline, so nothing is
lost during the wait. When the owner confirms, the project becomes Active (committed) and the
weighted line is retired in the same transaction, so the deal is counted exactly once.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.roles import CLIENT_PARTNER, COMMERCIAL_OWNER, SELLER_AE
from app.models import (
    Category,
    Customer,
    DealProjectLink,
    DraftConfirmation,
    HubspotOpportunity,
    PipelineEvent,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    RevenueRevision,
    User,
)
from app.pipeline.weighting import phase

DRAFT_STATUS = "Draft"
ACTIVE_STATUS = "Active"
SYNC_ACTOR = "hubspot-sync"
DRAFT_ID_PREFIX = "HS-"
VARIANCE_ALERT = Decimal("0.10")  # PRD: HubSpot amount vs committed schedule differing by >10%


class DraftError(Exception):
    """A confirmation was refused; `code` and the message are shown to the user."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def add_business_days(start: datetime, days: int) -> datetime:
    """Add whole weekdays (Mon-Fri). Public holidays are not modelled."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def owner_role_for(user: User) -> str | None:
    """The ownership role a deal owner takes on the draft, from the roles they actually hold."""
    names = {role.name for role in user.roles}
    for user_role, owner_role in (
        (SELLER_AE, "ae"),
        (CLIENT_PARTNER, "client_partner"),
        (COMMERCIAL_OWNER, "commercial_owner"),
    ):
        if user_role in names:
            return owner_role
    return None


def schedule_variance(
    amount: Decimal | None, schedule_total: Decimal, outside: Decimal
) -> dict[str, Any]:
    """Compare the deal amount with the schedule, allowing for months outside the window."""
    if amount is None or amount == 0:
        return {"deal_amount": amount, "schedule_total": schedule_total, "outside_window": outside,
                "variance_ratio": None, "exceeds_threshold": False}  # fmt: skip
    expected_in_window = amount - outside
    ratio = (abs(schedule_total - expected_in_window) / amount).quantize(Decimal("0.0001"))
    return {
        "deal_amount": amount,
        "schedule_total": schedule_total,
        "outside_window": outside,
        "variance_ratio": ratio,
        "exceeds_threshold": ratio > VARIANCE_ALERT,
    }


async def possible_duplicates(session: AsyncSession, customer_name: str) -> list[dict[str, Any]]:
    """Existing projects for the same customer, to show before a draft is confirmed."""
    rows = await session.execute(
        select(Project.id, Project.source_row_id, Project.name, Project.status)
        .join(Customer, Customer.id == Project.customer_id)
        .where(func.lower(Customer.name) == customer_name.lower(), Project.status != DRAFT_STATUS)
        .order_by(Project.name)
    )
    return [{"project_id": i, "source_row_id": r, "name": n, "status": s} for i, r, n, s in rows]


@dataclass
class DraftOutcome:
    project: Project | None
    reason: str | None = None  # why no draft was created
    outside_window: Decimal = Decimal("0.00")


async def create_draft(
    session: AsyncSession,
    deal: HubspotOpportunity,
    owner: User | None,
    months: list[date],
    now: datetime,
    business_days: int,
    sync_run_id: int | None,
) -> DraftOutcome:
    """Create the draft project for a Closed Won deal that has no project yet.

    Returns no project (and a reason) rather than guessing when the deal lacks what a project
    needs: a company name, or a project name that is already taken for that customer.
    """
    if not deal.company_resolved_name:
        return DraftOutcome(None, "company_unresolved")
    customer = await session.scalar(
        select(Customer).where(func.lower(Customer.name) == deal.company_resolved_name.lower())
    )
    if customer is None:
        customer = Customer(name=deal.company_resolved_name)
        session.add(customer)
        await session.flush()
    taken = await session.scalar(
        select(Project.id).where(Project.customer_id == customer.id, Project.name == deal.name)
    )
    if taken is not None:
        return DraftOutcome(None, "project_name_exists")

    duplicates = await possible_duplicates(session, customer.name)
    project = Project(
        source_row_id=f"{DRAFT_ID_PREFIX}{deal.hubspot_deal_id}",
        customer_id=customer.id,
        name=deal.name,
        practice=deal.practice,
        region=deal.region,
        status=DRAFT_STATUS,
        owners=[],
    )
    session.add(project)
    await session.flush()

    owner_role = owner_role_for(owner) if owner else None
    if owner is not None and owner_role is not None:
        project.owners.append(ProjectOwner(role=owner_role, user=owner))

    # Proposed schedule: the Closed Won amount at 100%, spread evenly from the expected start.
    phasing = phase(deal.amount, Decimal(1), deal.expected_start, deal.duration_months, months)
    revisions = [
        RevenueRevision(
            project_id=project.id, field="project", change_type="create", old_value=None,
            new_value=f"Draft from HubSpot deal {deal.hubspot_deal_id}", actor_label=SYNC_ACTOR,
        )
    ]  # fmt: skip
    for month, amount in sorted(phasing.monthly.items()):
        session.add(
            ProjectMonthlyRevenue(
                project_id=project.id, month=month, amount=amount, baseline_amount=None
            )
        )
        revisions.append(
            RevenueRevision(
                project_id=project.id, month=month, field="revenue", change_type="create",
                old_value=None, new_value=str(amount), actor_label=SYNC_ACTOR,
                note=f"Proposed from HubSpot deal {deal.hubspot_deal_id}",
            )
        )  # fmt: skip
    session.add_all(revisions)

    session.add(DealProjectLink(opportunity_id=deal.id, project_id=project.id, link_source="draft"))
    session.add(
        DraftConfirmation(
            project_id=project.id,
            opportunity_id=deal.id,
            owner_user_id=owner.id if owner and owner_role else None,
            requested_at=now,
            due_at=add_business_days(now, business_days),
            possible_duplicates=duplicates or None,
        )
    )
    session.add(
        PipelineEvent(
            event_type="draft_created", opportunity_id=deal.id, project_id=project.id,
            sync_run_id=sync_run_id, actor_label=SYNC_ACTOR,
            message=(
                f"Closed Won: draft project created for {customer.name}; confirmation requested"
                + (f" from {owner.name}" if owner and owner_role else " (no owner assigned)")
            ),
        )
    )  # fmt: skip
    await session.flush()
    return DraftOutcome(project, None, phasing.outside_window)


async def load_confirmation(session: AsyncSession, project_id: int) -> DraftConfirmation | None:
    return await session.scalar(
        select(DraftConfirmation).where(DraftConfirmation.project_id == project_id)
    )


async def confirm_draft(
    session: AsyncSession,
    project: Project,
    user: User,
    *,
    category: str | None,
    industry: str | None,
    acknowledge_duplicates: bool,
    now: datetime,
) -> DraftConfirmation:
    """Turn a draft into an Active committed project and retire the deal's weighted line.

    Raises DraftError for anything that should stop the confirmation. The caller commits, so the
    status change, the retirement event and the audit trail succeed or fail together.
    """
    confirmation = await load_confirmation(session, project.id)
    if confirmation is None or project.status != DRAFT_STATUS:
        raise DraftError("not_a_draft", "This project is not waiting for confirmation.", 409)
    if confirmation.confirmed_at is not None:
        raise DraftError("already_confirmed", "This draft has already been confirmed.", 409)

    scheduled = await session.scalar(
        select(func.count())
        .select_from(ProjectMonthlyRevenue)
        .where(ProjectMonthlyRevenue.project_id == project.id)
    )
    if not scheduled:
        raise DraftError(
            "empty_schedule",
            "Enter the monthly revenue schedule before confirming; the draft has no months yet.",
        )
    if confirmation.possible_duplicates and not acknowledge_duplicates:
        names = ", ".join(str(d["name"]) for d in confirmation.possible_duplicates)
        raise DraftError(
            "possible_duplicate",
            f"This customer already has committed project(s): {names}. Confirm that this is "
            "new work (acknowledge the possible duplicate) or edit the existing project instead.",
            409,
        )
    if category is not None:
        known = await session.scalar(select(Category.code).where(Category.code == category))
        if known is None:
            raise DraftError(
                "invalid_category", f"Category must be one of EE, EN, NN (got {category!r})."
            )

    changes: list[tuple[str, str | None, str | None]] = [("status", DRAFT_STATUS, ACTIVE_STATUS)]
    project.status = ACTIVE_STATUS
    if category is not None and category != project.category_code:
        changes.append(("category", project.category_code, category))
        project.category_code = category
    if industry is not None and industry != project.industry:
        changes.append(("industry", project.industry, industry))
        project.industry = industry
    for field_name, old, new in changes:
        session.add(
            RevenueRevision(
                project_id=project.id, field=field_name, change_type="update", old_value=old,
                new_value=new, actor_user_id=user.id, actor_label=user.name,
                note="Confirmed draft created from HubSpot Closed Won deal",
            )
        )  # fmt: skip

    confirmation.confirmed_at = now
    confirmation.confirmed_by_id = user.id
    confirmation.duplicates_acknowledged = bool(confirmation.possible_duplicates)
    session.add_all(
        [
            PipelineEvent(
                event_type="draft_confirmed", opportunity_id=confirmation.opportunity_id,
                project_id=project.id, actor_user_id=user.id, actor_label=user.name,
                from_value=DRAFT_STATUS, to_value=ACTIVE_STATUS,
                message=f"{user.name} confirmed the schedule; project is now committed",
            ),
            PipelineEvent(
                event_type="weighted_line_retired", opportunity_id=confirmation.opportunity_id,
                project_id=project.id, actor_user_id=user.id, actor_label=user.name,
                message="Weighted line retired: the deal is now counted as committed revenue",
            ),
        ]
    )  # fmt: skip
    await session.flush()
    return confirmation


async def load_project_for_confirm(session: AsyncSession, project_id: int) -> Project | None:
    return await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.owners).selectinload(ProjectOwner.user),
            selectinload(Project.customer),
        )
        .with_for_update(of=Project)
    )
