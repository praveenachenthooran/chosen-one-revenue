"""The PRD's seeded QA dataset: 20 known errors hidden among clean rows and look-alike decoys.

PRD acceptance: "Given a seeded dataset with 20 known errors (duplicates, stale rows, mismatches,
anomalies), when the QA agent runs, then findings cite evidence ... recall >= 90%, precision >= 80%".
No such dataset was supplied, so this one is built here with its ground truth stated explicitly.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RevenueRevision, User
from app.pipeline.source import SourceLink
from app.pipeline.sync import run_sync
from app.pipeline.weighting import add_months
from app.provisioning import grant
from tests.helpers import make_settings
from tests.pipeline_helpers import FakeSource, sdeal, snapshot
from tests.test_import import run_import
from tests.workbook_builder import WorkbookBuilder

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
FRESH = datetime(2026, 10, 15)  # "Last Updated" for a row that is up to date
OLD = datetime(2026, 8, 1)  # nearly three months before NOW

PEOPLE: list[tuple[str | None, str, list[str], str | None]] = [
    ("AC", "avery@corp.test", ["Client Partner"], None),
    ("JM", "jordan@corp.test", ["Client Partner"], None),
    ("Shah", "riley@corp.test", ["Seller / Account Executive"], None),
    ("AF", "alex@corp.test", ["Commercial Owner"], None),
    (None, "cro@corp.test", ["CRO / Sales Leadership"], "Casey CRO"),
    (None, "admin@corp.test", ["Administrator"], "Ada Admin"),
    (None, "fin@corp.test", ["Finance"], "Fin Ance"),
    (None, "res@corp.test", ["Delivery / Resourcing"], "Re Source"),
    (None, "sam@corp.test", ["Client Partner"], "Sam NotOwner"),
]

# (rule, key) for each of the 20 seeded errors.
TRUTH: set[tuple[str, str]] = {
    # 4 duplicates
    ("duplicate_project", "R3001+R3002"),
    ("duplicate_project", "R3003+R3004"),
    ("duplicate_project", "R3005+R3006"),
    ("duplicate_project", "R3007+R3008"),
    # 3 stale rows
    ("stale_row", "R3010"),
    ("stale_row", "R3011"),
    ("stale_row", "R3012"),
    # 4 unexplained month-over-month movements
    ("mom_anomaly", "R3020"),
    ("mom_anomaly", "R3021"),
    ("mom_anomaly", "R3022"),
    ("mom_anomaly", "R3023"),
    # 4 rows missing required fields
    ("missing_required", "R3030"),
    ("missing_required", "R3031"),
    ("missing_required", "R3032"),
    ("missing_required", "R3033"),
    # 3 Closed Won deals that are not in the committed forecast
    ("closed_won_missing", "901"),
    ("closed_won_missing", "902"),
    ("closed_won_missing", "903"),
    # 2 HubSpot amounts that disagree with the committed schedule by more than 10%
    ("amount_mismatch", "910"),
    ("amount_mismatch", "911"),
}


@dataclass
class Seed:
    session: AsyncSession
    source: FakeSource
    truth: set[tuple[str, str]] = field(default_factory=lambda: set(TRUTH))
    users: dict[str, int] = field(default_factory=lambda: {})


def build(builder: WorkbookBuilder) -> None:
    builder.months = [add_months(date(2026, 10, 1), i) for i in range(12)]

    def row(
        row_id: str, customer: str, project: str, amounts: Sequence[float | None], **extra: Any
    ) -> None:
        extra.setdefault("Last Updated", FRESH)
        builder.add(row_id, Customer=customer, Project=project, amounts=amounts, **extra)

    for i in range(50):  # clean rows: owned, complete, fresh and steady
        row(f"R{2000 + i}", f"Clean Customer {i:02d}", f"Clean Project {i:02d}", [10000.0] * 12)

    flat = [25000.0] * 12
    row("R3001", "Lakemoor Pharma", "Clinical Workflow", flat)
    row("R3002", "Lakemoor Pharma", "Clinical Workflows", flat)
    row("R3003", "Orion Bank", "Policy-Admin", flat)
    row("R3004", "Orion Bank", "Policy Admin", flat)
    row("R3005", "Vega Retail", "Billing Engine", flat)
    row("R3006", "Vega Retail", "Billing Engines", flat)
    row("R3007", "Altair Energy", "Mobile App", flat)
    row("R3008", "Altair Energy", "Mobile Apps", flat)

    for n in (10, 11, 12):
        row(
            f"R30{n}",
            f"Stale Customer {n}",
            f"Stale Project {n}",
            [20000.0] * 12,
            **{"Last Updated": OLD},
        )

    row("R3020", "Spike Customer", "Silverpine-like", [30000.0] * 5 + [51000.0] * 4 + [30000.0] * 3)
    row("R3021", "Cliff Customer", "Cliff Project", [40000.0] * 3 + [0.0] * 9)
    row("R3022", "Halving Customer", "Halving Project", [60000.0] * 6 + [30000.0] * 6)
    row("R3023", "Doubling Customer", "Doubling Project", [20000.0] * 8 + [45000.0] * 4)

    row("R3030", "Ownerless One", "No Owner A", [10000.0] * 12, **{"Client Partner": None})
    row("R3031", "Ownerless Two", "No Owner B", [10000.0] * 12, **{"Client Partner": None})
    row("R3032", "Uncategorised", "No Category", [10000.0] * 12, CATG=None)
    row("R3033", "Regionless", "No Region", [10000.0] * 12, Region=None)

    # Linked to HubSpot deals (see `deals`): 3040/3041 disagree, 3042/3043 agree closely enough.
    row("R3040", "Mismatch One", "Rollout A", [10000.0] * 12)  # schedule total 120,000
    row("R3041", "Mismatch Two", "Rollout B", [20000.0] * 12)  # schedule total 240,000
    row("R3042", "Matching One", "Rollout C", [10000.0] * 12)
    row("R3043", "Matching Two", "Rollout D", [10000.0] * 12)

    # Decoys: look like errors but are not.
    row("R3050", "Explained Customer", "Explained Spike", [30000.0] * 5 + [51000.0] * 7)
    row("R3051", "Milestone Customer", "Milestones", [0.0, 120000.0, 0.0, 0.0, 90000.0] + [0.0] * 7,
        **{"Billing Type": "Fixed fee"})  # fmt: skip
    row("R3052", "Rate Customer", "Rate Uplift", [25000.0] * 6 + [26250.0] * 6)  # under $5K
    row("R3053", "Paused Customer", "Paused Then Resumed", [0.0] * 3 + [34000.0] * 9)  # restart
    row(
        "R3054",
        "Done Customer",
        "Finished Work",
        [10000.0] * 12,
        Status="Completed",
        **{"Last Updated": OLD},
    )
    row("R3055", "Phase Customer", "Claims Platform Phase 2", [10000.0] * 12)
    row(
        "R3056", "Phase Customer", "Claims Platform Phase 3", [20000.0] * 12
    )  # similar name, other work


def deals() -> tuple[list[Any], list[SourceLink]]:
    deals = [
        sdeal("901", stage="Closed Won", company="Zenith Foods", name="Zenith - Rollout"),
        sdeal("902", stage="Closed Won", company="Borealis Media", name="Borealis - Platform"),
        # HubSpot gave only a company ID, so no draft project can be created.
        sdeal("903", stage="Closed Won", company=None, company_id="55555", name="Mystery Won"),
        sdeal("910", stage="Closed Won", company="Mismatch One", amount="500000", start=date(2026, 10, 1), duration=12),
        sdeal("911", stage="Closed Won", company="Mismatch Two", amount="90000", start=date(2026, 10, 1), duration=12),
        sdeal("912", stage="Closed Won", company="Matching One", amount="120000", start=date(2026, 10, 1), duration=12),
        sdeal("913", stage="Closed Won", company="Matching Two", amount="130000", start=date(2026, 10, 1), duration=12),
        sdeal("920", stage="Contracting", company="Pipeline Co", amount="60000"),  # an ordinary open deal
        sdeal("921", stage="Negotiation/Review", company="Other Pipeline Co", amount="80000"),
    ]  # fmt: skip
    links = [
        SourceLink("910", "R3040"),
        SourceLink("911", "R3041"),
        SourceLink("912", "R3042"),
        SourceLink("913", "R3043"),
    ]
    return deals, links


async def create_seed(session: AsyncSession, builder: WorkbookBuilder) -> Seed:
    build(builder)
    await run_import(session, builder)
    for code, email, roles, name in PEOPLE:
        await grant(session, email=email, roles=roles, code=code, name=name)
    source = FakeSource()
    seeded_deals, links = deals()
    source.current = snapshot(seeded_deals, links=links)
    report = await run_sync(session, source, make_settings(), now=NOW)
    assert report.status == "completed", report.error
    users = {u.email or "": u.id for u in await session.scalars(select(User))}

    # An explained movement: the owner recorded a reason code when making the change.
    from tests.reporting_helpers import project_id

    session.add(
        RevenueRevision(
            project_id=await project_id(session, "R3050"), month=date(2027, 3, 1), field="revenue",
            change_type="update", old_value="30000.00", new_value="51000.00",
            actor_user_id=users["avery@corp.test"], actor_label="Avery", reason_code="NEW_SOW",
            note="Signed change order",
        )
    )  # fmt: skip
    await session.commit()
    return Seed(session, source, users=users)
