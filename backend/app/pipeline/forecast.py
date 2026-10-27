"""Which deals count as weighted pipeline, and what they contribute each month.

Everything is computed here, on the server, from the deals, the configured stages and the links
to committed projects. The single rule that prevents double counting (PRD FR-14): a deal whose
linked project is *committed* (not a draft) never contributes weighted revenue.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Customer,
    DealProjectLink,
    DraftConfirmation,
    HubspotOpportunity,
    HubspotStage,
    ImportRun,
    Project,
    ProjectOwner,
    User,
)
from app.months import month_columns
from app.pipeline.drafts import DRAFT_STATUS
from app.pipeline.weighting import Phasing, phase

ZERO = Decimal("0.00")

# Display status of a deal in the forecast.
WEIGHTED = "weighted"  # counting as probability-weighted pipeline
AWAITING = "awaiting_confirmation"  # Closed Won, draft created, owner has not confirmed yet
WON_PENDING = "closed_won_pending"  # Closed Won but no draft could be created (see findings)
LINKED = "linked"  # represented by a committed project: no weighted line
LOST = "closed_lost"
EXCLUDED = "excluded"  # below the forecast stage, unknown stage or gone from HubSpot
COUNTING = (WEIGHTED, AWAITING, WON_PENDING)


@dataclass
class Window:
    months: list[date]  # every forecast month (matches the committed grid)
    open_months: list[date]  # months not yet closed: where pipeline is shown
    as_of: date | None


@dataclass
class DealFilters:
    owner: str | None = None  # user code
    practice: str | None = None
    region: str | None = None
    industry: str | None = None
    category: str | None = None
    customer: str | None = None
    q: str | None = None

    def active(self) -> bool:
        return any(vars(self).values())


@dataclass
class DealView:
    deal: HubspotOpportunity
    stage: HubspotStage | None
    owner: User | None
    linked_project: Project | None
    confirmation: DraftConfirmation | None
    industry: str | None
    status: str
    probability: Decimal | None = None
    phasing: Phasing | None = None
    findings: int = 0

    @property
    def counts(self) -> bool:
        return self.status in COUNTING

    @property
    def weighted_amount(self) -> Decimal:
        return self.phasing.weighted_amount if self.phasing and self.counts else ZERO

    @property
    def company(self) -> str | None:
        return self.deal.company_resolved_name


@dataclass
class Aggregate:
    weighted_by_month: dict[date, Decimal] = field(default_factory=lambda: {})
    deal_count: int = 0
    unphased_deals: int = 0
    unphased_weighted: Decimal = ZERO
    outside_window_weighted: Decimal = ZERO


async def forecast_window(session: AsyncSession) -> Window:
    months = await month_columns(session)
    as_of = await session.scalar(
        select(ImportRun.source_as_of)
        .where(ImportRun.status == "completed")
        .order_by(ImportRun.id.desc())
        .limit(1)
    )
    first_open = as_of.replace(day=1) if as_of else None
    open_months = [m for m in months if first_open is None or m >= first_open]
    return Window(months, open_months, as_of)


def _classify(
    deal: HubspotOpportunity,
    stage: HubspotStage | None,
    project: Project | None,
    confirmation: DraftConfirmation | None,
) -> str:
    if deal.lifecycle == "closed_lost":
        return LOST
    if deal.lifecycle == "excluded" or stage is None:
        return EXCLUDED
    if project is not None and project.status != DRAFT_STATUS:
        return LINKED  # never both committed and weighted
    if deal.lifecycle == "closed_won":
        return AWAITING if project is not None and confirmation is not None else WON_PENDING
    return WEIGHTED


async def load_views(session: AsyncSession, window: Window) -> list[DealView]:
    """Every deal with its stage, owner, link, draft, phasing and display status."""
    deals = list(
        await session.scalars(
            select(HubspotOpportunity)
            .options(
                selectinload(HubspotOpportunity.link)
                .selectinload(DealProjectLink.project)
                .selectinload(Project.owners)
                .selectinload(ProjectOwner.user),
            )
            .order_by(HubspotOpportunity.id)
        )
    )
    stages = {s.name: s for s in await session.scalars(select(HubspotStage))}
    owners = {
        u.id: u for u in await session.scalars(select(User).options(selectinload(User.roles)))
    }
    confirmations = {c.opportunity_id: c for c in await session.scalars(select(DraftConfirmation))}
    industry_rows = await session.execute(
        select(func.lower(Customer.name), Project.industry)
        .join(Project, Project.customer_id == Customer.id)
        .where(Project.industry.is_not(None))
        .distinct()
    )
    industries: dict[str, set[str]] = {}
    for name, industry in industry_rows:
        if industry:
            industries.setdefault(name, set()).add(industry)

    views: list[DealView] = []
    for deal in deals:
        stage = stages.get(deal.stage)
        project = deal.link.project if deal.link else None
        confirmation = confirmations.get(deal.id)
        status = _classify(deal, stage, project, confirmation)
        known = industries.get((deal.company_resolved_name or "").lower(), set())
        view = DealView(
            deal=deal,
            stage=stage,
            owner=owners.get(deal.owner_user_id) if deal.owner_user_id else None,
            linked_project=project,
            confirmation=confirmation,
            # Only when every project of that customer agrees; HubSpot has no industry field.
            industry=next(iter(known)) if len(known) == 1 else None,
            status=status,
        )
        if stage is not None and status in COUNTING:
            view.probability = stage.probability
            view.phasing = phase(
                deal.amount, stage.probability, deal.expected_start, deal.duration_months,
                window.open_months,
            )  # fmt: skip
        views.append(view)
    return views


def matches(view: DealView, f: DealFilters) -> bool:
    """The PRD's forecast filters applied to a deal. A deal lacking the attribute never matches
    a filter on it (HubSpot has no category, and industry is only known for existing customers)."""
    deal = view.deal
    if f.owner and not (view.owner and view.owner.code == f.owner):
        return False
    if f.practice and deal.practice != f.practice:
        return False
    if f.region and deal.region != f.region:
        return False
    if f.industry and view.industry != f.industry:
        return False
    if f.category:  # deals carry no EE/EN/NN category until a project exists
        category = view.linked_project.category_code if view.linked_project else None
        if category != f.category:
            return False
    if f.customer and (view.company or "").lower() != f.customer.lower():
        return False
    if f.q:
        needle = f.q.lower()
        if needle not in deal.name.lower() and needle not in (view.company or "").lower():
            return False
    return True


def aggregate(views: list[DealView]) -> Aggregate:
    result = Aggregate()
    for view in views:
        if not view.counts or view.phasing is None:
            continue
        result.deal_count += 1
        if not view.phasing.phaseable:
            result.unphased_deals += 1
            result.unphased_weighted += view.phasing.weighted_amount
            continue
        result.outside_window_weighted += view.phasing.outside_window
        for month, value in view.phasing.monthly.items():
            result.weighted_by_month[month] = result.weighted_by_month.get(month, ZERO) + value
    return result
