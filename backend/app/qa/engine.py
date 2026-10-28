"""Running the QA checks: load a read-only picture of the forecast, detect, and record findings.

Repeated runs never duplicate findings: each finding has a fingerprint (rule + subject) and one
row for life. A finding still detected is refreshed; one no longer detected is marked `resolved`
(and reopened if it comes back); accepted and dismissed findings stay as the human left them.
Every change is written to the append-only `qa_finding_events`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.locks import QA_LOCK_KEY
from app.models import (
    DraftConfirmation,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    QaFinding,
    QaFindingEvent,
    QaRun,
    RevenueRevision,
    User,
)
from app.pipeline.forecast import forecast_window, load_views
from app.qa.rules import Detected, QaData, detect, j

SYSTEM_ACTOR = "qa-engine"


class QaBusyError(Exception):
    """Another QA run holds the lock."""


@dataclass
class QaResult:
    run_id: int
    status: str  # completed | failed
    new: int = 0
    seen: int = 0
    reopened: int = 0
    resolved: int = 0
    open_total: int = 0
    error: str | None = None


async def load_data(session: AsyncSession, settings: Settings, now: datetime) -> QaData:
    window = await forecast_window(session)
    projects = list(
        await session.scalars(
            select(Project)
            .options(
                selectinload(Project.customer),
                selectinload(Project.owners).selectinload(ProjectOwner.user),
            )
            .order_by(Project.id)
        )
    )
    revenue: dict[int, dict[date, Decimal]] = defaultdict(dict)
    for project_id, month, amount in await session.execute(
        select(
            ProjectMonthlyRevenue.project_id,
            ProjectMonthlyRevenue.month,
            ProjectMonthlyRevenue.amount,
        )
    ):
        revenue[project_id][month] = amount

    reasons: dict[tuple[int, date], RevenueRevision] = {}
    last_edit: dict[int, datetime] = {}
    for revision in await session.scalars(
        select(RevenueRevision)
        .where(RevenueRevision.actor_user_id.is_not(None))
        .order_by(RevenueRevision.created_at, RevenueRevision.id)
    ):
        last_edit[revision.project_id] = revision.created_at
        if revision.month is not None:
            reasons[(revision.project_id, revision.month)] = revision

    views = await load_views(session, window)
    users = {u.id: u for u in await session.scalars(select(User))}
    confirmations = {c.opportunity_id: c for c in await session.scalars(select(DraftConfirmation))}
    return QaData(
        now=now,
        as_of=window.as_of,
        open_months=window.open_months,
        projects=projects,
        revenue=dict(revenue),
        reasons=reasons,
        last_human_edit=last_edit,
        views=views,
        confirmations=confirmations,
        users=users,
        settings=settings,
    )


def _event(
    finding: QaFinding, run: QaRun | None, kind: str, message: str, actor: str = SYSTEM_ACTOR,
    actor_id: int | None = None, details: dict[str, object] | None = None,
) -> QaFindingEvent:  # fmt: skip
    return QaFindingEvent(
        finding_id=finding.id, run_id=run.id if run else None, event_type=kind,
        actor_user_id=actor_id, actor_label=actor, message=message, details=details or {},
    )  # fmt: skip


def _apply(finding: QaFinding, d: Detected, run: QaRun, now: datetime) -> None:
    finding.project_id = d.project_id
    finding.opportunity_id = d.opportunity_id
    finding.related_project_ids = d.related_project_ids
    finding.severity = d.severity
    finding.title = d.title[:300]
    finding.summary = d.summary
    finding.evidence = j(d.evidence)
    finding.suggested_fix = d.suggested_fix
    finding.financial_impact = d.impact
    finding.routed_to_id = d.route_to
    finding.last_run_id = run.id
    finding.last_seen_at = now


async def run_qa(
    session: AsyncSession,
    settings: Settings,
    *,
    trigger: str,
    now: datetime | None = None,
    actor: User | None = None,
    blocking: bool = False,
) -> QaResult:
    """Run every rule and record the outcome (the caller commits).

    `blocking=False` raises `QaBusyError` if another run is in progress; the nightly job waits.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if blocking:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": QA_LOCK_KEY})
    else:
        got = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": QA_LOCK_KEY})
        if not got:
            raise QaBusyError("A QA run is already in progress.")

    data = await load_data(session, settings, now)
    run = QaRun(
        trigger=trigger, status="running", started_at=now,
        triggered_by_id=actor.id if actor else None,
        triggered_by_label=actor.name if actor else "nightly job", as_of=data.as_of,
    )  # fmt: skip
    session.add(run)
    await session.flush()

    detected = {d.fingerprint: d for d in detect(data)}
    existing = {f.fingerprint: f for f in await session.scalars(select(QaFinding))}
    result = QaResult(run.id, "completed")
    events: list[QaFindingEvent] = []

    for fingerprint, d in detected.items():
        finding = existing.get(fingerprint)
        if finding is None:
            finding = QaFinding(fingerprint=fingerprint, rule_code=d.rule_code, status="open")
            _apply(finding, d, run, now)
            finding.first_run_id = run.id
            session.add(finding)
            await session.flush()
            events.append(
                _event(finding, run, "created", f"Raised by the {d.rule_code} check: {d.title}",
                       details={"impact": str(d.impact), "routed_to": d.route_to})
            )  # fmt: skip
            result.new += 1
            continue
        previous = (finding.routed_to_id, finding.severity, finding.financial_impact)
        reopened = finding.status == "resolved"
        _apply(finding, d, run, now)
        if reopened:
            finding.status = "open"
            events.append(_event(finding, run, "reopened", "The condition is detected again."))
            result.reopened += 1
        elif finding.status == "open":
            if previous[0] != finding.routed_to_id:
                events.append(
                    _event(finding, run, "routed", "Routed to a different owner.",
                           details={"from": previous[0], "to": finding.routed_to_id})
                )  # fmt: skip
            if (previous[1], previous[2]) != (finding.severity, finding.financial_impact):
                events.append(
                    _event(finding, run, "updated", "Severity or financial impact changed.",
                           details={"impact": str(finding.financial_impact),
                                    "severity": finding.severity})
                )  # fmt: skip
            result.seen += 1
        else:
            result.seen += (
                1  # accepted or dismissed: the decision stands, only the evidence refreshes
            )

    for fingerprint, finding in existing.items():
        if fingerprint not in detected and finding.status == "open":
            finding.status = "resolved"
            finding.last_run_id = run.id
            events.append(
                _event(finding, run, "auto_resolved", "No longer detected by the checks.")
            )
            result.resolved += 1

    session.add_all(events)
    await session.flush()
    open_total = await session.scalar(
        select(func.count()).select_from(QaFinding).where(QaFinding.status == "open")
    )
    result.open_total = open_total or 0
    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    run.findings_new = result.new
    run.findings_seen = result.seen
    run.findings_reopened = result.reopened
    run.findings_resolved = result.resolved
    run.findings_open = result.open_total
    run.rule_counts = dict(Counter(d.rule_code for d in detected.values()))
    await session.flush()
    return result


async def run_qa_safely(
    session: AsyncSession,
    settings: Settings,
    *,
    trigger: str,
    now: datetime | None = None,
    actor: User | None = None,
    blocking: bool = False,
) -> QaResult:
    """Run and commit. Any failure rolls the whole run back and is recorded as a failed run, so
    existing findings and the forecast are untouched. `QaBusyError` is passed on."""
    started = (now or datetime.now(UTC)).astimezone(UTC)
    actor_id = actor.id if actor else None
    label = actor.name if actor else "nightly job"
    try:
        result = await run_qa(
            session, settings, trigger=trigger, now=now, actor=actor, blocking=blocking
        )
        await session.commit()
        return result
    except QaBusyError:
        await session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - the run must report, never crash the caller
        await session.rollback()
        failed = QaRun(
            trigger=trigger, status="failed", started_at=started, finished_at=datetime.now(UTC),
            triggered_by_id=actor_id, triggered_by_label=label,
            error_message=f"{type(exc).__name__}: {exc}"[:2000],
        )  # fmt: skip
        session.add(failed)
        await session.commit()
        return QaResult(failed.id, "failed", error=failed.error_message)
