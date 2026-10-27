"""HubSpot sync: deal lifecycle, Closed Won drafts, idempotency, duplicates and failure handling."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DealProjectLink,
    DraftConfirmation,
    HubspotOpportunity,
    HubspotStage,
    HubspotSyncFinding,
    HubspotSyncRun,
    PipelineEvent,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    RevenueRevision,
)
from app.pipeline import sync as sync_module
from app.pipeline.drafts import add_business_days
from app.pipeline.forecast import DealView, forecast_window, load_views
from app.pipeline.source import SourceDeal, SourceError, SourceLink
from app.pipeline.sync import SyncReport, run_sync
from app.pipeline.weighting import add_months
from app.provisioning import grant
from tests.helpers import make_settings
from tests.pipeline_helpers import NOW, STAGES, FakeSource, sdeal, snapshot
from tests.test_import import run_import
from tests.workbook_builder import WorkbookBuilder

PEOPLE = [
    ("AC", "avery@corp.test", ["Client Partner"]),
    ("JM", "jordan@corp.test", ["Client Partner"]),
    ("Shah", "riley@corp.test", ["Seller / Account Executive"]),
    ("AF", "alex@corp.test", ["Commercial Owner"]),
    ("PN", "priya@corp.test", ["Client Partner", "Commercial Owner"]),
]


@dataclass
class World:
    session: AsyncSession
    builder: WorkbookBuilder


@pytest_asyncio.fixture
async def world(session: AsyncSession, builder: WorkbookBuilder) -> World:
    builder.months = [add_months(date(2026, 10, 1), i) for i in range(12)]
    builder.add("R1", Customer="Kestrel Life", Project="Policy Admin", Industry="Insurance")
    builder.add(
        "R2",
        Customer="Granitepeak Telecom",
        Project="MuleSoft API Program",
        **{"Client Partner": "JM"},
        Industry="Broad Markets",
    )
    builder.add(
        "R3", Customer="Initech", Project="Existing Work", CATG="EN", Industry="Banking & FS"
    )
    await run_import(session, builder)
    for code, email, roles in PEOPLE:
        await grant(session, email=email, roles=roles, code=code, name=None)
    return World(session, builder)


async def sync(
    session: AsyncSession, source: FakeSource, now: datetime = NOW, **kwargs: object
) -> SyncReport:
    return await run_sync(session, source, make_settings(), now=now, **kwargs)  # type: ignore[arg-type]


async def views(session: AsyncSession) -> dict[str, DealView]:
    window = await forecast_window(session)
    return {v.deal.hubspot_deal_id: v for v in await load_views(session, window)}


async def events(session: AsyncSession, kind: str | None = None) -> list[PipelineEvent]:
    stmt = select(PipelineEvent).order_by(PipelineEvent.id)
    if kind:
        stmt = stmt.where(PipelineEvent.event_type == kind)
    session.expire_all()
    return list(await session.scalars(stmt))


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


# ---- first sync and the weighting rule ---------------------------------------------------


async def test_first_sync_stores_deals_stages_and_a_run_record(world: World) -> None:
    s = world.session
    report = await sync(s, FakeSource(snapshot([sdeal("1"), sdeal("2", stage="Presentation")])))

    assert (report.status, report.deals_seen, report.deals_created) == ("completed", 2, 2)
    assert await count(s, HubspotOpportunity) == 2
    assert {st.name for st in await s.scalars(select(HubspotStage))} == {x.name for x in STAGES}
    run = await s.scalar(select(HubspotSyncRun))
    assert run is not None and run.status == "completed" and run.source_ref == "fake.xlsx"
    assert (run.deals_created, run.triggered_by_label) == (2, "system")


async def test_weighted_pipeline_follows_the_prd_rule(world: World) -> None:
    """$120,000 at 50% starting Nov 1 for 6 months: $10,000 a month, November to April."""
    await sync(world.session, FakeSource(snapshot([sdeal("1")])))
    view = (await views(world.session))["1"]

    assert view.status == "weighted" and view.probability == Decimal("0.5000")
    assert view.weighted_amount == Decimal("60000.00")
    assert view.phasing is not None
    assert list(view.phasing.monthly.values()) == [Decimal("10000.00")] * 6
    assert min(view.phasing.monthly) == date(2026, 11, 1)
    assert max(view.phasing.monthly) == date(2027, 4, 1)


async def test_four_decimal_amounts_are_stored_exactly(world: World) -> None:
    await sync(world.session, FakeSource(snapshot([sdeal("1", amount="308409.0508")])))
    world.session.expire_all()
    deal = await world.session.scalar(select(HubspotOpportunity))
    assert deal is not None and deal.amount == Decimal("308409.0508")


# ---- idempotency ---------------------------------------------------------------------------


async def test_repeating_a_sync_changes_nothing(world: World) -> None:
    s = world.session
    source = FakeSource(
        snapshot([sdeal("1"), sdeal("2", stage="Closed Won", company="Brightharbor")])
    )
    first = await sync(s, source)
    before = (
        await count(s, HubspotOpportunity),
        await count(s, Project),
        await count(s, DraftConfirmation),
        await count(s, DealProjectLink),
        await count(s, ProjectMonthlyRevenue),
        await count(s, RevenueRevision),
        await count(s, PipelineEvent),
    )

    second = await sync(s, source, now=NOW + timedelta(minutes=5))
    third = await sync(s, source, now=NOW + timedelta(minutes=10))

    after = (
        await count(s, HubspotOpportunity),
        await count(s, Project),
        await count(s, DraftConfirmation),
        await count(s, DealProjectLink),
        await count(s, ProjectMonthlyRevenue),
        await count(s, RevenueRevision),
        await count(s, PipelineEvent),
    )
    assert after == before
    assert (first.deals_created, first.drafts_created) == (2, 1)
    for report in (second, third):
        assert (report.deals_created, report.deals_updated, report.deals_unchanged) == (0, 0, 2)
        assert report.drafts_created == 0
    assert await count(s, HubspotSyncRun) == 3  # each run is still recorded


async def test_changed_deal_fields_update_in_place_without_duplicating(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1", amount="100000")])))
    report = await sync(s, FakeSource(snapshot([sdeal("1", amount="150000", duration=12)])))

    assert (report.deals_created, report.deals_updated) == (0, 1)
    assert await count(s, HubspotOpportunity) == 1
    view = (await views(s))["1"]
    assert view.deal.amount == Decimal("150000.0000") and view.deal.duration_months == 12
    assert view.weighted_amount == Decimal("75000.00")


# ---- lifecycle transitions ----------------------------------------------------------------


async def test_stage_change_between_forecast_stages_is_logged_and_reweighted(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1", stage="Presentation")])))
    assert (await views(s))["1"].weighted_amount == Decimal("24000.00")  # 20%
    await sync(s, FakeSource(snapshot([sdeal("1", stage="Contracting")])))

    assert (await views(s))["1"].weighted_amount == Decimal("96000.00")  # 80%
    (changed,) = await events(s, "stage_changed")
    assert (changed.from_value, changed.to_value) == ("Presentation", "Contracting")


async def test_closed_lost_removes_the_weighted_line_and_logs_it(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1")])))
    report = await sync(s, FakeSource(snapshot([sdeal("1", stage="Closed Lost")])))

    view = (await views(s))["1"]
    assert (
        view.status == "closed_lost" and not view.counts and view.weighted_amount == Decimal("0.00")
    )
    assert report.lines_retired == 1
    (event,) = await events(s, "deal_closed_lost")
    assert "weighted line removed" in event.message and event.sync_run_id == report.run_id
    assert event.from_value == "weighted" and event.actor_label == "hubspot-sync"


async def test_moving_below_the_forecast_stage_excludes_the_deal_and_logs_it(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1")])))
    report = await sync(s, FakeSource(snapshot([sdeal("1", stage="Qualified")])))

    view = (await views(s))["1"]
    assert view.status == "excluded" and view.deal.lifecycle_reason == "below_forecast_stage"
    assert report.lines_retired == 1
    (event,) = await events(s, "deal_removed_from_forecast")
    assert "below the forecast stage" in event.message

    await sync(s, FakeSource(snapshot([sdeal("1", stage="Proposal/Price Quote")])))
    assert (await views(s))["1"].status == "weighted"  # and back again
    assert len(await events(s, "deal_entered_forecast")) == 2


async def test_deals_below_the_forecast_stage_never_enter_the_forecast(world: World) -> None:
    await sync(world.session, FakeSource(snapshot([sdeal("1", stage="Qualified")])))
    view = (await views(world.session))["1"]
    assert view.status == "excluded" and view.weighted_amount == Decimal("0.00")
    assert await events(world.session) == []  # nothing entered, so nothing to log


async def test_a_deal_missing_from_a_complete_feed_has_left_the_forecast(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1"), sdeal("2")])))
    await sync(s, FakeSource(snapshot([sdeal("1")])))

    seen = await views(s)
    assert seen["1"].status == "weighted"
    assert (
        seen["2"].status == "excluded" and seen["2"].deal.lifecycle_reason == "absent_from_source"
    )
    assert [e.event_type for e in await events(s, "deal_removed_from_forecast")] == [
        "deal_removed_from_forecast"
    ]


async def test_a_partial_feed_does_not_remove_deals_it_does_not_mention(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1"), sdeal("2")])))
    await sync(s, FakeSource(snapshot([sdeal("1")], complete=False)))
    assert (await views(s))["2"].status == "weighted"


async def test_an_unconfigured_stage_excludes_the_deal_with_a_finding(world: World) -> None:
    report = await sync(world.session, FakeSource(snapshot([sdeal("1", stage="Mystery Stage")])))
    view = (await views(world.session))["1"]
    assert view.status == "excluded" and view.deal.lifecycle_reason == "unknown_stage"
    assert "unknown_stage" in [f.code for f in report.findings]


async def test_sync_never_overwrites_admin_maintained_stage_probabilities(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1")])))
    stage = await s.scalar(select(HubspotStage).where(HubspotStage.name == "Negotiation/Review"))
    assert stage is not None
    stage.probability, stage.in_forecast = Decimal("0.7000"), True
    await s.commit()

    await sync(s, FakeSource(snapshot([sdeal("1")])))
    s.expire_all()
    stage = await s.scalar(select(HubspotStage).where(HubspotStage.name == "Negotiation/Review"))
    assert stage is not None and stage.probability == Decimal("0.7000")  # source says 0.5
    assert (await views(s))["1"].weighted_amount == Decimal("84000.00")


# ---- owners and findings -------------------------------------------------------------------


async def test_owner_names_match_ignoring_case_and_spacing(world: World) -> None:
    await sync(
        world.session,
        FakeSource(
            snapshot([sdeal("1", owner="  riley   SHAH "), sdeal("2", owner="Nobody Known")])
        ),
    )
    seen = await views(world.session)
    assert seen["1"].owner is not None and seen["1"].owner.code == "Shah"
    assert seen["2"].owner is None


async def test_validation_findings_are_recorded_for_incomplete_and_suspicious_deals(
    world: World,
) -> None:
    s = world.session
    deals = [
        sdeal("1", start=None, duration=None),  # cannot be phased
        sdeal(
            "2",
            company="Corriveau",
            company_id="55",
            amount="36500",
            close=datetime(2026, 12, 1, 9, 1, tzinfo=UTC),
        ),
        sdeal(
            "3",
            company="Corriveau",
            company_id="55",
            amount="36500",
            close=datetime(2026, 12, 1, 12, 33, tzinfo=UTC),
        ),
        sdeal("4", close=datetime(2026, 2, 5, tzinfo=UTC)),  # close date already past
        sdeal("5", owner="Ghost Owner"),
    ]
    report = await sync(s, FakeSource(snapshot(deals)))

    by_deal: dict[str, set[str]] = {}
    for finding in report.findings:
        by_deal.setdefault(finding.deal_id or "", set()).add(finding.code)
    assert "possible_duplicate_deal" in by_deal["2"] and "possible_duplicate_deal" in by_deal["3"]
    assert "stale_close_date" in by_deal["4"] and "owner_unresolved" in by_deal["5"]
    stored = await s.scalar(select(func.count()).select_from(HubspotSyncFinding))
    assert stored == len(report.findings)
    # Unphaseable deals are kept out of the monthly numbers, not guessed.
    view = (await views(s))["1"]
    assert view.phasing is not None and not view.phasing.phaseable and view.phasing.monthly == {}
    assert view.phasing.weighted_amount == Decimal("60000.00")  # still reported as an amount


# ---- Closed Won -> draft project -----------------------------------------------------------


def won(deal_id: str = "10", **kwargs: object) -> SourceDeal:
    name = (
        "Brightharbor - Claims AI Pilot" if deal_id == "10" else f"Brightharbor - Pilot {deal_id}"
    )
    base: dict[str, object] = {"stage": "Closed Won", "company": "Brightharbor Assurance"}
    base.update(kwargs)
    return sdeal(deal_id, name=name, **base)  # type: ignore[arg-type]


async def test_closed_won_creates_a_draft_committed_project_and_requests_confirmation(
    world: World,
) -> None:
    s = world.session
    report = await sync(s, FakeSource(snapshot([won()])))

    assert report.drafts_created == 1
    project = await s.scalar(select(Project).where(Project.source_row_id == "HS-10"))
    assert project is not None
    assert (project.status, project.name, project.practice, project.region) == (
        "Draft",
        "Brightharbor - Claims AI Pilot",
        "APPIAN",
        "NA",
    )
    # Owner: Riley Shah holds the Seller / AE role, so is assigned as the AE.
    owners = list(
        await s.scalars(select(ProjectOwner).where(ProjectOwner.project_id == project.id))
    )
    assert [(o.role, o.user_id) for o in owners] and owners[0].role == "ae"
    # Proposed schedule: the full amount (100%) over six months from November.
    rows = list(
        await s.scalars(
            select(ProjectMonthlyRevenue)
            .where(ProjectMonthlyRevenue.project_id == project.id)
            .order_by(ProjectMonthlyRevenue.month)
        )
    )
    assert [r.amount for r in rows] == [Decimal("20000.00")] * 6
    assert all(r.baseline_amount is None for r in rows)  # added in the app, not the import
    # Confirmation requested: due in two business days (Thu 1 Oct -> Mon 5 Oct).
    confirmation = await s.scalar(select(DraftConfirmation))
    assert confirmation is not None and confirmation.confirmed_at is None
    assert confirmation.due_at == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    link = await s.scalar(select(DealProjectLink))
    assert link is not None and link.link_source == "draft" and link.project_id == project.id
    # Audit trail and log.
    assert {
        r.actor_label
        for r in await s.scalars(
            select(RevenueRevision).where(RevenueRevision.project_id == project.id)
        )
    } == {"hubspot-sync"}
    (created,) = await events(s, "draft_created")
    assert "confirmation requested from Riley Shah" in created.message


async def test_until_confirmed_the_draft_is_not_committed_and_the_deal_still_counts_once(
    world: World,
) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([won()])))
    view = (await views(s))["10"]
    assert view.status == "awaiting_confirmation" and view.counts
    assert view.probability == Decimal("1.0000") and view.weighted_amount == Decimal("120000.00")
    assert view.linked_project is not None and view.linked_project.status == "Draft"


async def test_closed_won_already_in_the_forecast_links_instead_of_drafting(world: World) -> None:
    """The workbook's Deal_ChosenOne_Map: a deal tied to an existing row is counted once."""
    s = world.session
    source = FakeSource(
        snapshot([won("20", company="Granitepeak Telecom")], links=[SourceLink("20", "R2")])
    )
    report = await sync(s, source)

    assert report.drafts_created == 0 and await count(s, DraftConfirmation) == 0
    view = (await views(s))["20"]
    assert view.status == "linked" and not view.counts and view.weighted_amount == Decimal("0.00")
    assert view.linked_project is not None and view.linked_project.source_row_id == "R2"
    link = await s.scalar(select(DealProjectLink))
    assert link is not None and link.link_source == "workbook_map"
    assert len(await events(s, "deal_linked")) == 1
    await sync(s, source)
    assert await count(s, DealProjectLink) == 1  # repeating does not duplicate the link


async def test_links_to_unknown_projects_or_deals_are_findings_not_guesses(world: World) -> None:
    report = await sync(
        world.session,
        FakeSource(
            snapshot([won("20")], links=[SourceLink("20", "R9999"), SourceLink("404", "R1")])
        ),
    )
    assert [f.code for f in report.findings].count("link_target_missing") == 2


async def test_closed_won_with_an_unknown_company_creates_no_draft_and_keeps_counting(
    world: World,
) -> None:
    s = world.session
    report = await sync(
        s, FakeSource(snapshot([won("30", company=None, company_id="29146887085")]))
    )

    assert report.drafts_created == 0 and await count(s, Project) == 3  # only the imported ones
    assert "draft_blocked_company_unresolved" in [f.code for f in report.findings]
    view = (await views(s))["30"]
    assert view.status == "closed_won_pending" and view.counts  # not dropped from the forecast


async def test_closed_won_whose_project_name_is_taken_creates_no_draft(world: World) -> None:
    report = await sync(
        world.session,
        FakeSource(
            snapshot([sdeal("40", stage="Closed Won", name="Existing Work", company="Initech")])
        ),
    )
    assert report.drafts_created == 0
    assert "draft_blocked_project_name_exists" in [f.code for f in report.findings]


async def test_closed_won_with_an_unknown_owner_still_drafts_but_flags_it(world: World) -> None:
    s = world.session
    report = await sync(s, FakeSource(snapshot([won("50", owner="Stranger Danger")])))
    project = await s.scalar(select(Project).where(Project.source_row_id == "HS-50"))
    assert project is not None
    assert not list(
        await s.scalars(select(ProjectOwner).where(ProjectOwner.project_id == project.id))
    )
    assert "draft_owner_unassigned" in [f.code for f in report.findings]


async def test_owner_role_follows_the_roles_a_person_actually_holds(world: World) -> None:
    s = world.session
    await sync(
        s, FakeSource(snapshot([won("60", owner="Priya Nair"), won("61", owner="Avery Chen")]))
    )
    rows = await s.execute(
        select(Project.source_row_id, ProjectOwner.role)
        .join(ProjectOwner, ProjectOwner.project_id == Project.id)
        .where(Project.source_row_id.in_(["HS-60", "HS-61"]))
    )
    assert dict(rows.all()) == {"HS-60": "client_partner", "HS-61": "client_partner"}


async def test_possible_duplicates_of_the_same_customer_are_recorded_for_the_confirmer(
    world: World,
) -> None:
    await sync(world.session, FakeSource(snapshot([won("70", company="Initech")])))
    confirmation = await world.session.scalar(select(DraftConfirmation))
    assert confirmation is not None and confirmation.possible_duplicates is not None
    assert [d["source_row_id"] for d in confirmation.possible_duplicates] == ["R3"]


async def test_an_overdue_draft_is_escalated_exactly_once(world: World) -> None:
    s = world.session
    source = FakeSource(snapshot([won()]))
    await sync(s, source)
    assert await events(s, "draft_overdue") == []

    later = add_business_days(NOW, 2) + timedelta(hours=1)
    await sync(s, source, now=later)
    await sync(s, source, now=later + timedelta(days=1))
    (alert,) = await events(s, "draft_overdue")
    assert "2 business days" in alert.message


async def test_a_won_deal_that_reopens_with_an_unconfirmed_draft_is_flagged(world: World) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([won()])))
    report = await sync(s, FakeSource(snapshot([won(stage="Contracting")])))
    assert "won_deal_reopened" in [f.code for f in report.findings]
    assert await count(s, Project) == 4  # the draft is kept for a human to decide


def test_business_days_skip_weekends() -> None:
    friday = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
    assert add_business_days(friday, 2) == datetime(2026, 10, 6, 15, 0, tzinfo=UTC)  # Tuesday
    assert add_business_days(NOW, 0) == NOW


# ---- manual edits and imported data are never touched --------------------------------------


async def test_sync_never_overwrites_manual_revenue_or_the_import_baseline(world: World) -> None:
    s = world.session
    source = FakeSource(snapshot([won(), sdeal("1")]))
    await sync(s, source)
    draft = await s.scalar(select(Project).where(Project.source_row_id == "HS-10"))
    r1 = await s.scalar(select(Project).where(Project.source_row_id == "R1"))
    assert draft is not None and r1 is not None
    draft_id = draft.id
    # A person edits a month of the draft and of an imported project.
    edited = {}
    for project, month in ((draft, date(2026, 12, 1)), (r1, date(2026, 11, 1))):
        row = await s.scalar(
            select(ProjectMonthlyRevenue).where(
                ProjectMonthlyRevenue.project_id == project.id, ProjectMonthlyRevenue.month == month
            )
        )
        assert row is not None
        row.amount = Decimal("77777.00")
        edited[project.id] = (month, row.baseline_amount)
    await s.commit()
    baseline_before = await s.scalar(select(func.sum(ProjectMonthlyRevenue.baseline_amount)))

    # HubSpot now says something different about the same Closed Won deal.
    await sync(s, FakeSource(snapshot([won(amount="999999", duration=3), sdeal("1", amount="1")])))

    s.expire_all()
    for project_id, (month, baseline) in edited.items():
        row = await s.scalar(
            select(ProjectMonthlyRevenue).where(
                ProjectMonthlyRevenue.project_id == project_id, ProjectMonthlyRevenue.month == month
            )
        )
        assert (
            row is not None
            and row.amount == Decimal("77777.00")
            and row.baseline_amount == baseline
        )
    assert (
        await s.scalar(select(func.sum(ProjectMonthlyRevenue.baseline_amount))) == baseline_before
    )
    rows = await s.scalar(
        select(func.count())
        .select_from(ProjectMonthlyRevenue)
        .where(ProjectMonthlyRevenue.project_id == draft_id)
    )
    assert rows == 6  # the draft schedule was proposed once and is now the owner's to manage


# ---- failures never block or damage the forecast -------------------------------------------


async def test_a_source_failure_keeps_the_last_good_data_and_records_a_failed_run(
    world: World,
) -> None:
    s = world.session
    good = FakeSource(snapshot([sdeal("1")]))
    await sync(s, good)
    before = (await views(s))["1"].weighted_amount

    report = await sync(
        s, FakeSource(error=SourceError("HubSpot is unreachable")), now=NOW + timedelta(hours=1)
    )

    assert report.status == "failed" and report.error == "HubSpot is unreachable"
    runs = list(await s.scalars(select(HubspotSyncRun).order_by(HubspotSyncRun.id)))
    assert [r.status for r in runs] == ["completed", "failed"]
    assert runs[1].error_message == "HubSpot is unreachable"
    assert (await views(s))["1"].weighted_amount == before  # untouched
    assert (await views(s))["1"].deal.lifecycle == "weighted"


async def test_unexpected_errors_are_contained_and_reported(world: World) -> None:
    report = await sync(world.session, FakeSource(error=RuntimeError("boom")))
    assert report.status == "failed" and report.error == "boom"


async def test_a_failure_part_way_through_rolls_back_everything_that_run_did(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = world.session
    await sync(s, FakeSource(snapshot([sdeal("1")])))
    deals_before, projects_before = await count(s, HubspotOpportunity), await count(s, Project)

    async def explode(*_: object, **__: object) -> None:
        raise RuntimeError("draft creation failed")

    monkeypatch.setattr(sync_module, "create_draft", explode)
    report = await sync(
        s,
        FakeSource(snapshot([sdeal("1", amount="1"), sdeal("2"), won()])),
        now=NOW + timedelta(hours=1),
    )

    assert report.status == "failed"
    assert await count(s, HubspotOpportunity) == deals_before  # deal 2 was not kept
    assert await count(s, Project) == projects_before
    deal = await s.scalar(
        select(HubspotOpportunity).where(HubspotOpportunity.hubspot_deal_id == "1")
    )
    assert deal is not None and deal.amount == Decimal(
        "120000.0000"
    )  # the amount change rolled back
    assert [
        r.status for r in await s.scalars(select(HubspotSyncRun).order_by(HubspotSyncRun.id))
    ] == [
        "completed",
        "failed",
    ]


async def test_committed_forecast_is_unaffected_by_a_failed_first_sync(world: World) -> None:
    s = world.session
    imported = await s.scalar(select(func.sum(ProjectMonthlyRevenue.amount)))
    await sync(s, FakeSource(error=SourceError("down")))
    assert await s.scalar(select(func.sum(ProjectMonthlyRevenue.amount))) == imported
    assert await count(s, HubspotOpportunity) == 0
