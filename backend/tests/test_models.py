"""Schema tests: relationships, constraints, and agreement between models and migrations."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import (
    Base,
    Category,
    Customer,
    DealProjectLink,
    ForecastSnapshot,
    ForecastSnapshotLine,
    HubspotOpportunity,
    MonthlyActual,
    OwnerAttestation,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    QaFinding,
    RevenueRevision,
    Role,
    User,
)

MONTH = date(2026, 11, 1)


async def make_project(session: AsyncSession, row_id: str = "R1") -> Project:
    customer = await session.scalar(select(Customer).where(Customer.name == "Acme"))
    if customer is None:
        customer = Customer(name="Acme")
        session.add(customer)
        await session.flush()
    project = Project(
        source_row_id=row_id, customer_id=customer.id, name=f"Portal {row_id}", status="Active",
        owners=[],
    )  # fmt: skip
    session.add(project)
    await session.flush()
    return project


async def test_models_and_migrations_agree(engine: AsyncEngine) -> None:
    """Fails if a model changed without a matching Alembic migration."""

    def diff(connection: object) -> list[object]:
        context = MigrationContext.configure(connection)  # type: ignore[arg-type]
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(diff) == []


async def test_project_relationships(session: AsyncSession) -> None:
    project = await make_project(session)
    avery = User(code="AC", name="Avery Chen", roles=[Role(name="Client Partner")])
    session.add(avery)
    await session.flush()
    project.owners.append(ProjectOwner(role="client_partner", user=avery))
    session.add_all(
        [
            ProjectMonthlyRevenue(project_id=project.id, month=MONTH, amount=Decimal("10.50")),
            MonthlyActual(project_id=project.id, month=MONTH, amount=Decimal("9.99")),
            RevenueRevision(
                project_id=project.id, month=MONTH, field="revenue", change_type="update",
                old_value="1", new_value="2", actor_user_id=avery.id, actor_label="test",
            ),
            OwnerAttestation(
                project_id=project.id, attested_by_id=avery.id, period=date(2026, 10, 1),
                attested_at=datetime.now(UTC),
            ),
        ]
    )  # fmt: skip
    await session.commit()

    loaded = await session.scalar(select(Project).where(Project.id == project.id))
    assert loaded is not None
    await session.refresh(loaded, ["owners", "customer"])
    assert loaded.customer.name == "Acme"
    assert [(o.role, o.user.name) for o in loaded.owners] == [("client_partner", "Avery Chen")]
    assert (await session.scalars(select(Customer))).all()[0].name == "Acme"


async def test_snapshot_lines_hold_a_copy_of_the_forecast(session: AsyncSession) -> None:
    project = await make_project(session)
    snapshot = ForecastSnapshot(label="2026-10-01", snapshot_date=date(2026, 10, 1))
    snapshot.lines.append(
        ForecastSnapshotLine(project_id=project.id, month=MONTH, amount=Decimal("100.00"))
    )
    session.add(snapshot)
    await session.commit()
    assert snapshot.kind == "monthly"

    session.add(
        ForecastSnapshotLine(snapshot_id=snapshot.id, project_id=project.id, month=MONTH, amount=1)
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_deal_links_to_at_most_one_project(session: AsyncSession) -> None:
    first, second = await make_project(session, "R1"), await make_project(session, "R2")
    deal = HubspotOpportunity(
        hubspot_deal_id="57018801551", name="SOW-2", stage="Closed Won",
        probability=Decimal("0.8000"), amount=Decimal("252000.00"),
    )  # fmt: skip
    session.add(deal)
    await session.flush()
    session.add(DealProjectLink(opportunity_id=deal.id, project_id=first.id))
    await session.commit()

    session.add(DealProjectLink(opportunity_id=deal.id, project_id=second.id))
    with pytest.raises(IntegrityError):  # a deal can never be counted against two projects
        await session.commit()


@pytest.mark.parametrize(
    "build",
    [
        lambda pid: ProjectMonthlyRevenue(project_id=pid, month=date(2026, 11, 2), amount=1),
        lambda pid: ProjectMonthlyRevenue(project_id=pid, month=MONTH, amount=Decimal("-1.00")),
        lambda pid: MonthlyActual(project_id=pid, month=date(2026, 11, 15), amount=1),
        lambda pid: ProjectOwner(project_id=pid, user_id=1, role="boss"),
        lambda pid: RevenueRevision(
            project_id=pid, field="x", change_type="rename", actor_label="t"
        ),
        lambda pid: QaFinding(project_id=pid, rule_code="r", summary="s", status="dismissed"),
    ],
    ids=[
        "month-not-first", "negative-amount", "actual-month-not-first", "bad-owner-role",
        "bad-change-type", "dismissed-without-reason",
    ],
)  # fmt: skip
async def test_check_constraints_reject_bad_data(session: AsyncSession, build: object) -> None:
    project = await make_project(session)
    session.add(User(id=1, code="X", name="X"))
    await session.flush()
    session.add(build(project.id))  # type: ignore[operator]
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_unique_constraints_prevent_duplicates(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add_all([ProjectMonthlyRevenue(project_id=project.id, month=MONTH, amount=1),
                     Category(code="EE", description="a")])  # fmt: skip
    await session.commit()

    for duplicate in (
        ProjectMonthlyRevenue(project_id=project.id, month=MONTH, amount=2),
        Project(source_row_id="R1", customer_id=project.customer_id, name="Other", status="x"),
        Project(source_row_id="R9", customer_id=project.customer_id, name=project.name, status="x"),
        Customer(name="Acme"),
        Category(code="EE", description="b"),
    ):
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_qa_finding_review_requires_reviewer_and_reason(session: AsyncSession) -> None:
    project = await make_project(session)
    reviewer = User(code="CRO", name="Demo CRO")
    session.add(reviewer)
    await session.flush()
    finding = QaFinding(project_id=project.id, rule_code="duplicate", summary="Looks duplicated")
    session.add(finding)
    await session.commit()
    assert finding.status == "open" and finding.severity == "warning" and finding.evidence == {}

    finding.status, finding.reviewed_by_id, finding.review_reason = "dismissed", reviewer.id, "ok"
    await session.commit()
