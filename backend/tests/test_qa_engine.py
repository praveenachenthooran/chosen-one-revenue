"""The deterministic QA engine: the PRD's seeded 20-error set, idempotency, lifecycle and rules."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import (
    JobRun,
    Project,
    QaFinding,
    QaFindingEvent,
    QaRun,
    RevenueRevision,
)
from app.qa import engine as engine_module
from app.qa.engine import QaBusyError, run_qa, run_qa_safely
from app.qa.jobs import run_nightly
from app.qa.rules import RULES
from tests.helpers import make_settings
from tests.qa_seed import NOW, TRUTH, Seed, create_seed
from tests.reporting_helpers import project_id, set_amount
from tests.workbook_builder import WorkbookBuilder

SETTINGS = make_settings()


@pytest_asyncio.fixture
async def seed(session: AsyncSession, builder: WorkbookBuilder) -> Seed:
    return await create_seed(session, builder)


async def findings(session: AsyncSession, status: str | None = "open") -> list[QaFinding]:
    stmt = select(QaFinding).order_by(QaFinding.id)
    if status:
        stmt = stmt.where(QaFinding.status == status)
    return list(await session.scalars(stmt))


def keyed(rows: list[QaFinding]) -> dict[tuple[str, str], QaFinding]:
    """Findings by (rule, subject key), reading the subject out of the evidence."""
    out: dict[tuple[str, str], QaFinding] = {}
    for f in rows:
        e = f.evidence
        if f.rule_code == "duplicate_project":
            key = "+".join(sorted(r["row_id"] for r in e["rows"]))
        elif "deal" in e and f.rule_code in (
            "closed_won_missing",
            "amount_mismatch",
            "deal_missing_fields",
        ):
            key = e["deal"]["hubspot_deal_id"]
        else:
            key = e["project"]["row_id"]
        out[(f.rule_code, key)] = f
    return out


async def go(seed: Seed, **kw: object) -> object:
    result = await run_qa(seed.session, SETTINGS, trigger="manual", now=NOW, **kw)  # type: ignore[arg-type]
    await seed.session.commit()
    return result


# ---- the PRD acceptance: 20 seeded errors, recall >= 90%, precision >= 80% -------------------


async def test_seeded_dataset_meets_the_prd_recall_and_precision_targets(seed: Seed) -> None:
    await go(seed)
    detected = set(keyed(await findings(seed.session)))

    true_positives = detected & TRUTH
    recall = len(true_positives) / len(TRUTH)
    precision = len(true_positives) / len(detected)
    print(
        f"seeded errors {len(TRUTH)}, findings {len(detected)}, recall {recall:.0%}, precision {precision:.0%}"
    )
    assert len(TRUTH) == 20
    assert recall >= 0.90 and precision >= 0.80
    # The rules are exact on this set: every seeded error found, nothing else raised.
    assert detected == TRUTH, (TRUTH - detected, detected - TRUTH)


async def test_decoys_are_not_flagged(seed: Seed) -> None:
    await go(seed)
    flagged_rows = {k for (_, k) in keyed(await findings(seed.session))}
    for decoy in (
        "R3050",
        "R3051",
        "R3052",
        "R3053",
        "R3054",
        "R3055",
        "R3056",
        "R3042",
        "R3043",
        "912",
        "913",
        "920",
        "921",
    ):
        assert decoy not in flagged_rows, decoy
    assert not any(
        "R3055" in k or "R3056" in k for k in flagged_rows
    )  # similar names, different work


async def test_every_finding_cites_evidence_a_fix_and_an_owner_route(seed: Seed) -> None:
    await go(seed)
    for f in await findings(seed.session):
        assert f.evidence and f.suggested_fix and f.title and f.summary
        assert f.rule_code in RULES and f.severity in ("error", "warning")
        assert f.financial_impact >= 0 and f.fingerprint and f.status == "open"


# ---- individual rules ------------------------------------------------------------------------


async def test_duplicate_finding_details(seed: Seed) -> None:
    await go(seed)
    f = keyed(await findings(seed.session))[("duplicate_project", "R3001+R3002")]
    assert f.severity == "error" and f.financial_impact == Decimal(
        "300000.00"
    )  # 12 x 25,000 counted twice
    assert {r["row_id"] for r in f.evidence["rows"]} == {"R3001", "R3002"}
    assert f.evidence["equal_forward_months"] == f.evidence["shared_forward_months"] == 12
    assert f.project_id == await project_id(seed.session, "R3002")  # the later row, the likely copy
    assert sorted(f.related_project_ids) == sorted(
        [await project_id(seed.session, "R3001"), await project_id(seed.session, "R3002")]
    )
    assert f.routed_to_id == seed.users["avery@corp.test"]


async def test_anomaly_evidence_and_impact(seed: Seed) -> None:
    await go(seed)
    rows = keyed(await findings(seed.session))
    spike = rows[("mom_anomaly", "R3020")]
    moves = spike.evidence["movements"]
    assert [m["month"] for m in moves] == ["2027-03-01", "2027-07-01"]  # up, then back down
    assert moves[0]["change"] == "21000.00" and moves[0]["change_percent"] == "70.0"
    assert spike.financial_impact == Decimal("21000.00") and "no reason recorded" in spike.summary
    cliff = rows[("mom_anomaly", "R3021")]
    assert cliff.evidence["movements"][0]["after"] == "0.00" and cliff.financial_impact == Decimal(
        "40000.00"
    )
    assert ("mom_anomaly", "R3050") not in rows  # explained with a reason code


async def test_a_reason_code_clears_the_anomaly_and_a_note_alone_does_not(seed: Seed) -> None:
    pid = await project_id(seed.session, "R3022")
    seed.session.add(
        RevenueRevision(project_id=pid, month=date(2027, 4, 1), field="revenue", change_type="update",
                        actor_user_id=seed.users["avery@corp.test"], actor_label="Avery", note="just because")
    )  # fmt: skip
    await seed.session.commit()
    await go(seed)
    assert ("mom_anomaly", "R3022") in keyed(
        await findings(seed.session)
    )  # a note is not a reason code

    seed.session.add(
        RevenueRevision(project_id=pid, month=date(2027, 4, 1), field="revenue", change_type="update",
                        actor_user_id=seed.users["avery@corp.test"], actor_label="Avery", reason_code="RAMP_DOWN")
    )  # fmt: skip
    await seed.session.commit()
    await go(seed)
    assert ("mom_anomaly", "R3022") not in keyed(await findings(seed.session))


async def test_mismatch_numbers(seed: Seed) -> None:
    await go(seed)
    f = keyed(await findings(seed.session))[("amount_mismatch", "910")]
    assert (
        f.evidence["schedule_total"] == "120000.00" and f.evidence["difference_percent"] == "76.00"
    )
    assert f.financial_impact == Decimal("380000.00")
    assert f.routed_to_id == seed.users["avery@corp.test"]  # the linked project's owner


async def test_mismatch_boundary_is_strictly_more_than_ten_percent(seed: Seed) -> None:
    pid = await project_id(seed.session, "R3042")  # deal 912 = 120,000
    await set_amount(
        seed.session, "R3042", date(2026, 10, 1), "22000.00"
    )  # schedule 132,000: exactly 10%
    await go(seed)
    assert ("amount_mismatch", "912") not in keyed(await findings(seed.session))
    await set_amount(seed.session, "R3042", date(2026, 10, 1), "22001.00")  # 10.0008%
    await go(seed)
    assert ("amount_mismatch", "912") in keyed(await findings(seed.session))
    assert pid


async def test_stale_boundary_is_strictly_more_than_thirty_days(seed: Seed) -> None:
    pid = await project_id(seed.session, "R2000")
    for last, expect in (
        (date(2026, 9, 27), False),
        (date(2026, 9, 26), True),
    ):  # 30 and 31 days before
        await seed.session.execute(
            update(Project).where(Project.id == pid).values(source_last_updated=last)
        )
        await seed.session.commit()
        await go(seed)
        assert (("stale_row", "R2000") in keyed(await findings(seed.session))) is expect, last


async def test_a_recent_edit_in_the_app_clears_a_stale_row(seed: Seed) -> None:
    await go(seed)
    assert ("stale_row", "R3010") in keyed(await findings(seed.session))
    seed.session.add(
        RevenueRevision(project_id=await project_id(seed.session, "R3010"), month=date(2026, 11, 1),
                        field="revenue", change_type="update", actor_user_id=seed.users["avery@corp.test"],
                        actor_label="Avery", created_at=NOW - timedelta(days=2))
    )  # fmt: skip
    await seed.session.commit()
    await go(seed)
    assert ("stale_row", "R3010") not in keyed(await findings(seed.session))
    resolved = {f.title for f in await findings(seed.session, "resolved")}
    assert any("Not updated" in t for t in resolved)


async def test_missing_required_fields_and_routing(seed: Seed) -> None:
    await go(seed)
    rows = keyed(await findings(seed.session))
    no_owner = rows[("missing_required", "R3030")]
    assert (
        no_owner.severity == "error" and no_owner.routed_to_id is None
    )  # leadership assigns an owner
    assert "owner" in no_owner.evidence["missing"][0]
    assert rows[("missing_required", "R3032")].evidence["missing"] == ["category (EE/EN/NN)"]
    assert rows[("missing_required", "R3033")].evidence["missing"] == ["region"]
    assert rows[("missing_required", "R3033")].routed_to_id == seed.users["avery@corp.test"]


async def test_closed_won_not_committed(seed: Seed) -> None:
    await go(seed)
    rows = keyed(await findings(seed.session))
    drafted = rows[("closed_won_missing", "901")]
    assert drafted.evidence["draft_project"]["status"] == "Draft" and drafted.severity == "warning"
    assert (
        drafted.financial_impact == Decimal("120000.00")
        and drafted.routed_to_id == seed.users["riley@corp.test"]
    )
    blocked = rows[("closed_won_missing", "903")]
    assert blocked.severity == "error" and blocked.evidence["draft_project"] is None
    assert "no draft project could be created" in blocked.summary


async def test_an_overdue_draft_is_an_error(seed: Seed) -> None:
    await seed.session.execute(
        text("UPDATE draft_confirmations SET due_at = :d"), {"d": NOW - timedelta(days=1)}
    )
    await seed.session.commit()
    await go(seed)
    assert keyed(await findings(seed.session))[("closed_won_missing", "901")].severity == "error"


# ---- repeated runs, lifecycle and the audit trail --------------------------------------------


async def test_repeated_runs_do_not_duplicate_findings_or_events(seed: Seed) -> None:
    first = await go(seed)
    count = len(await findings(seed.session, None))
    events = await seed.session.scalar(select(func.count()).select_from(QaFindingEvent))
    second = await go(seed)
    third = await go(seed)

    assert (first.new, second.new, third.new) == (20, 0, 0)  # type: ignore[attr-defined]
    assert (second.seen, third.seen) == (20, 20)  # type: ignore[attr-defined]
    assert len(await findings(seed.session, None)) == count == 20
    assert (
        await seed.session.scalar(select(func.count()).select_from(QaFindingEvent)) == events
    )  # no noise
    runs = list(await seed.session.scalars(select(QaRun).order_by(QaRun.id)))
    assert [r.status for r in runs] == ["completed"] * 3 and runs[0].findings_new == 20
    assert runs[0].rule_counts["duplicate_project"] == 4 and runs[2].findings_open == 20


async def test_a_finding_is_resolved_when_fixed_and_reopened_if_it_returns(seed: Seed) -> None:
    await go(seed)
    pid = await project_id(seed.session, "R3002")

    async def rename(name: str) -> None:
        await seed.session.execute(update(Project).where(Project.id == pid).values(name=name))
        await seed.session.commit()

    await rename("Something Entirely Different")  # the copy is renamed into real, separate work
    fixed = await go(seed)
    assert fixed.resolved == 1  # type: ignore[attr-defined]
    assert ("duplicate_project", "R3001+R3002") in keyed(await findings(seed.session, "resolved"))

    await rename("Clinical Workflows")
    back = await go(seed)
    assert back.reopened == 1 and back.new == 0  # type: ignore[attr-defined]
    reopened = keyed(await findings(seed.session))[("duplicate_project", "R3001+R3002")]
    events = await seed.session.scalars(
        select(QaFindingEvent)
        .where(QaFindingEvent.finding_id == reopened.id)
        .order_by(QaFindingEvent.id)
    )
    assert [e.event_type for e in events] == ["created", "auto_resolved", "reopened"]
    assert len(await findings(seed.session, None)) == 20  # still one row for it


async def test_decided_findings_stay_decided_across_runs(seed: Seed) -> None:
    await go(seed)
    target = (await findings(seed.session))[0]
    target.status, target.reviewed_by_id, target.reviewed_at = (
        "dismissed",
        seed.users["cro@corp.test"],
        NOW,
    )
    target.review_reason = "Known and accepted"
    await seed.session.commit()
    await go(seed)
    await seed.session.refresh(target)
    assert target.status == "dismissed" and target.review_reason == "Known and accepted"
    assert len(await findings(seed.session, None)) == 20


async def test_events_are_append_only_in_the_database(seed: Seed) -> None:
    await go(seed)
    with pytest.raises(DBAPIError, match="immutable"):
        await seed.session.execute(text("UPDATE qa_finding_events SET message = 'x'"))
    await seed.session.rollback()
    with pytest.raises(DBAPIError, match="immutable"):
        await seed.session.execute(text("DELETE FROM qa_finding_events"))


async def test_review_fields_are_enforced_by_the_database(seed: Seed) -> None:
    await go(seed)
    with pytest.raises(DBAPIError):  # dismissed without a reviewer and reason
        await seed.session.execute(text("UPDATE qa_findings SET status = 'dismissed' WHERE id = 1"))
    await seed.session.rollback()


async def test_the_engine_never_changes_revenue_or_audit_history(seed: Seed) -> None:
    before_revenue = (
        await seed.session.execute(
            text("SELECT sum(amount), count(*) FROM project_monthly_revenue")
        )
    ).one()
    before_revisions = await seed.session.scalar(select(func.count()).select_from(RevenueRevision))
    await go(seed)
    await go(seed)
    after = (
        await seed.session.execute(
            text("SELECT sum(amount), count(*) FROM project_monthly_revenue")
        )
    ).one()
    assert tuple(after) == tuple(before_revenue)
    assert (
        await seed.session.scalar(select(func.count()).select_from(RevenueRevision))
        == before_revisions
    )


async def test_a_failed_run_is_recorded_and_leaves_findings_untouched(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    await go(seed)
    before = [(f.id, f.status, f.last_run_id) for f in await findings(seed.session, None)]

    def boom(*_: object) -> list[object]:
        raise RuntimeError("rule exploded")

    monkeypatch.setattr(engine_module, "detect", boom)
    result = await run_qa_safely(seed.session, SETTINGS, trigger="manual", now=NOW)
    assert result.status == "failed" and "rule exploded" in (result.error or "")
    monkeypatch.undo()
    assert [(f.id, f.status, f.last_run_id) for f in await findings(seed.session, None)] == before
    runs = list(await seed.session.scalars(select(QaRun).order_by(QaRun.id)))
    assert [r.status for r in runs] == ["completed", "failed"] and runs[1].error_message
    assert (
        await run_qa_safely(seed.session, SETTINGS, trigger="manual", now=NOW)
    ).status == "completed"


async def test_only_one_run_at_a_time(seed: Seed, engine: AsyncEngine) -> None:
    other = async_sessionmaker(engine, expire_on_commit=False)()
    async with other:
        await other.execute(text("SELECT pg_advisory_xact_lock(7301003)"))
        with pytest.raises(QaBusyError):
            await run_qa(seed.session, SETTINGS, trigger="manual", now=NOW)
        await other.rollback()
    await seed.session.rollback()
    assert (
        await run_qa_safely(seed.session, SETTINGS, trigger="manual", now=NOW)
    ).status == "completed"


# ---- the nightly job ---------------------------------------------------------------------------


async def test_nightly_job_runs_checks_records_the_job_and_is_idempotent(seed: Seed) -> None:
    first = await run_nightly(seed.session, SETTINGS, now=NOW)
    second = await run_nightly(seed.session, SETTINGS, now=NOW + timedelta(days=1))
    assert (first.new, second.new, second.open_total) == (20, 0, 20)
    jobs = list(
        await seed.session.scalars(
            select(JobRun).where(JobRun.job == "nightly_qa").order_by(JobRun.id)
        )
    )
    assert [j.status for j in jobs] == ["completed", "completed"] and "20 new" in jobs[0].message
    runs = list(await seed.session.scalars(select(QaRun).order_by(QaRun.id)))
    assert {r.trigger for r in runs} == {"scheduled"} and runs[
        0
    ].triggered_by_label == "nightly job"


async def test_nightly_job_reports_a_failure(seed: Seed, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> list[object]:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(engine_module, "detect", boom)
    result = await run_nightly(seed.session, SETTINGS, now=NOW)
    assert result.status == "failed"
    job = await seed.session.scalar(select(JobRun).where(JobRun.job == "nightly_qa"))
    assert job is not None and job.status == "failed" and "db hiccup" in job.message
