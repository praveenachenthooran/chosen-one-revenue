"""Synchronize HubSpot deals into PostgreSQL (read-only with respect to HubSpot).

One sync is one transaction: either everything it learned is stored, or (on any failure) none of
it is and a failed run is recorded while the last good data stays exactly as it was. Running it
again over unchanged input changes nothing. Deals are keyed by their HubSpot Record ID, and manual
edits to project revenue are never touched (a sync only creates *draft* projects).
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import (
    DealProjectLink,
    DraftConfirmation,
    HubspotOpportunity,
    HubspotStage,
    HubspotSyncFinding,
    HubspotSyncRun,
    PipelineEvent,
    Project,
    User,
)
from app.months import month_columns
from app.pipeline.drafts import SYNC_ACTOR, create_draft
from app.pipeline.source import DealSource, SourceDeal, SourceFinding, SourceSnapshot

logger = logging.getLogger(__name__)

CONTRIBUTING = ("weighted", "closed_won")


@dataclass
class SyncReport:
    run_id: int
    status: str
    source: str
    deals_seen: int = 0
    deals_created: int = 0
    deals_updated: int = 0
    deals_unchanged: int = 0
    drafts_created: int = 0
    lines_retired: int = 0
    findings: list[SourceFinding] = field(default_factory=lambda: [])
    error: str | None = None


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def lifecycle_for(stage: HubspotStage | None) -> tuple[str, str | None]:
    """The forecast state implied by a deal's stage and the configured forecast-stage rule."""
    if stage is None:
        return "excluded", "unknown_stage"
    if stage.kind == "lost":
        return "closed_lost", None
    if stage.kind == "won":
        return "closed_won", None
    if not stage.in_forecast:
        return "excluded", "below_forecast_stage"
    return "weighted", None


def _tracked(deal: HubspotOpportunity) -> tuple[Any, ...]:
    return (
        deal.name,
        deal.company_name,
        deal.company_resolved_name,
        deal.company_hubspot_id,
        deal.owner_name,
        deal.owner_user_id,
        deal.stage,
        deal.amount,
        deal.close_date,
        deal.expected_start,
        deal.duration_months,
        deal.practice,
        deal.region,
        deal.lifecycle,
        deal.lifecycle_reason,
    )


async def run_sync(
    session: AsyncSession,
    source: DealSource,
    settings: Settings,
    *,
    actor: User | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """Run one sync. Never raises for a source or data problem: it returns a failed report."""
    started = now or datetime.now(UTC)
    label = actor.name if actor else "system"
    actor_id = actor.id if actor else None  # read now: the object expires on rollback
    run = HubspotSyncRun(
        source=source.name,
        started_at=started,
        status="running",
        triggered_by_id=actor_id,
        triggered_by_label=label,
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    report = SyncReport(run_id=run_id, status="running", source=source.name)

    try:
        snapshot = await asyncio.to_thread(source.fetch)
        run.source_ref = snapshot.source_ref
        await _apply(session, run, snapshot, settings, started, report)
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        report.status = "completed"
        await session.commit()
    except Exception as exc:
        await session.rollback()
        message = str(exc)[:500] or exc.__class__.__name__
        logger.warning("HubSpot sync failed: %s", exc.__class__.__name__)
        session.add(
            HubspotSyncRun(
                source=source.name,
                started_at=started,
                finished_at=datetime.now(UTC),
                status="failed",
                error_message=message,
                triggered_by_id=actor_id,
                triggered_by_label=label,
            )
        )
        await session.commit()
        report = SyncReport(run_id=run_id, status="failed", source=source.name, error=message)
    return report


async def _apply(
    session: AsyncSession,
    run: HubspotSyncRun,
    snapshot: SourceSnapshot,
    settings: Settings,
    now: datetime,
    report: SyncReport,
) -> None:
    findings: list[SourceFinding] = list(snapshot.findings)

    def event(
        kind: str,
        deal: HubspotOpportunity,
        message: str,
        old: str | None = None,
        new: str | None = None,
    ) -> None:
        session.add(
            PipelineEvent(
                event_type=kind,
                opportunity_id=deal.id,
                sync_run_id=run.id,
                actor_label=SYNC_ACTOR,
                from_value=old,
                to_value=new,
                message=message,
            )
        )

    # --- stages: seeded from the source once, never overwritten (admins own the probabilities)
    stages = {s.name: s for s in await session.scalars(select(HubspotStage))}
    had_stages = bool(stages)  # a later new stage is worth a note; the first seeding is not
    for stage in snapshot.stages:
        if stage.name not in stages:
            row = HubspotStage(
                name=stage.name,
                probability=stage.probability,
                kind=stage.kind,
                in_forecast=stage.in_forecast,
                position=stage.position,
                source=f"{snapshot.source}: {snapshot.source_ref or 'unknown'}"
                + (f" ({stage.note})" if stage.note else ""),
                updated_at=now,
            )
            session.add(row)
            stages[stage.name] = row
            if had_stages:
                findings.append(
                    SourceFinding("info", "stage_added", f"New stage '{stage.name}' added.")
                )
    await session.flush()

    users = list(await session.scalars(select(User).options(selectinload(User.roles))))
    by_name: dict[str, list[User]] = {}
    for user in users:
        by_name.setdefault(_norm(user.name), []).append(user)

    existing = {d.hubspot_deal_id: d for d in await session.scalars(select(HubspotOpportunity))}
    in_snapshot: set[str] = set()
    deals_by_source: dict[str, HubspotOpportunity] = {}

    for source_deal in snapshot.deals:
        in_snapshot.add(source_deal.deal_id)
        deal = existing.get(source_deal.deal_id)
        owner, owner_findings = _resolve_owner(source_deal, by_name)
        findings += owner_findings
        stage = stages.get(source_deal.stage)
        lifecycle, reason = lifecycle_for(stage)
        if stage is None:
            findings.append(
                SourceFinding(
                    "warning",
                    "unknown_stage",
                    f"Stage '{source_deal.stage}' is not configured; deal excluded.",
                    source_deal.deal_id,
                )
            )

        if deal is None:
            deal = HubspotOpportunity(
                hubspot_deal_id=source_deal.deal_id,
                name=source_deal.name,
                stage=source_deal.stage,
                first_seen_at=now,
            )
            session.add(deal)
            before: tuple[Any, ...] | None = None
            old_lifecycle: str | None = None
            old_stage: str | None = None
        else:
            before = _tracked(deal)
            old_lifecycle, old_stage = deal.lifecycle, deal.stage

        deal.name = source_deal.name
        deal.company_name = source_deal.company_raw
        deal.company_resolved_name = source_deal.company_name
        deal.company_hubspot_id = source_deal.company_id
        deal.owner_name = source_deal.owner_name
        deal.owner_user_id = owner.id if owner else None
        deal.stage = source_deal.stage
        deal.amount = source_deal.amount
        deal.close_date = source_deal.close_date
        deal.expected_start = source_deal.expected_start
        deal.duration_months = source_deal.duration_months
        deal.practice = source_deal.practice
        deal.region = source_deal.region
        deal.raw = source_deal.raw
        deal.lifecycle, deal.lifecycle_reason = lifecycle, reason
        deal.last_synced_at = now
        await session.flush()
        deals_by_source[source_deal.deal_id] = deal

        if before is None:
            report.deals_created += 1
        elif before != _tracked(deal):
            report.deals_updated += 1
        else:
            report.deals_unchanged += 1

        _record_transition(event, deal, old_lifecycle, old_stage, report)

    report.deals_seen = len(snapshot.deals)

    # --- deals that are no longer in a complete feed have left the forecast
    if snapshot.complete:
        for deal_id, deal in existing.items():
            if deal_id in in_snapshot or deal.lifecycle not in CONTRIBUTING:
                continue
            old = deal.lifecycle
            deal.lifecycle, deal.lifecycle_reason = "excluded", "absent_from_source"
            report.lines_retired += 1
            event(
                "deal_removed_from_forecast",
                deal,
                "No longer returned by HubSpot at or beyond the forecast stage",
                old,
                "excluded",
            )

    # --- explicit deal -> project links supplied by the source
    projects = {p.source_row_id: p for p in await session.scalars(select(Project))}
    links = {link.opportunity_id: link for link in await session.scalars(select(DealProjectLink))}
    for link in snapshot.links:
        deal = deals_by_source.get(link.deal_id)
        project = projects.get(link.project_row_id)
        if deal is None or project is None:
            findings.append(
                SourceFinding(
                    "warning",
                    "link_target_missing",
                    f"Cannot link deal {link.deal_id} to {link.project_row_id}: not found.",
                    link.deal_id,
                )
            )
        elif deal.id not in links:
            links[deal.id] = DealProjectLink(
                opportunity_id=deal.id, project_id=project.id, link_source="workbook_map"
            )
            session.add(links[deal.id])
            await session.flush()
            event(
                "deal_linked",
                deal,
                f"Linked to existing project {project.source_row_id}; counted once, as committed",
            )
        elif links[deal.id].project_id != project.id:
            findings.append(
                SourceFinding(
                    "warning",
                    "link_conflict",
                    f"Deal {link.deal_id} is already linked to a different project.",
                    link.deal_id,
                )
            )
    await session.flush()

    # --- Closed Won with no project yet -> draft project + confirmation request
    months = await month_columns(session)
    refreshed = {d.hubspot_deal_id: d for d in await session.scalars(select(HubspotOpportunity))}
    for source_deal in snapshot.deals:
        deal = refreshed[source_deal.deal_id]
        if deal.lifecycle != "closed_won" or deal.id in links:
            continue
        owner = next(
            iter(by_name.get(_norm(deal.owner_name), []) if _norm(deal.owner_name) else []), None
        )
        outcome = await create_draft(
            session, deal, owner, months, now, settings.draft_confirmation_business_days, run.id
        )
        if outcome.project is None:
            findings.append(
                SourceFinding(
                    "warning",
                    f"draft_blocked_{outcome.reason}",
                    {
                        "company_unresolved": (
                            "Closed Won, but the company name is unknown (HubSpot gave only "
                            "an ID), so no draft project was created."
                        ),
                        "project_name_exists": (
                            "Closed Won, but this customer already has a project with the "
                            "same name; no draft was created."
                        ),
                    }.get(outcome.reason or "", "No draft project could be created."),
                    deal.hubspot_deal_id,
                )
            )
            continue
        report.drafts_created += 1
        if outcome.outside_window:
            findings.append(
                SourceFinding(
                    "info",
                    "schedule_outside_window",
                    f"{outcome.outside_window} of the deal falls outside the forecast months "
                    "and is not in the proposed schedule.",
                    deal.hubspot_deal_id,
                )
            )
        if source_deal.owner_name and outcome.project and not outcome.project.owners:
            findings.append(
                SourceFinding(
                    "warning",
                    "draft_owner_unassigned",
                    f"Deal owner '{source_deal.owner_name}' could not be assigned as a project "
                    "owner (unknown person, or no editing role).",
                    deal.hubspot_deal_id,
                )
            )

    links = {link.opportunity_id: link for link in await session.scalars(select(DealProjectLink))}
    # --- data-quality findings across the freshly synced deals
    findings += _quality_findings(list(refreshed.values()), snapshot.as_of or now.date())
    for deal in refreshed.values():
        link = links.get(deal.id)
        if deal.lifecycle != "closed_won" and link is not None and link.link_source == "draft":
            confirmation = await session.scalar(
                select(DraftConfirmation).where(DraftConfirmation.opportunity_id == deal.id)
            )
            if confirmation is not None and confirmation.confirmed_at is None:
                findings.append(
                    SourceFinding(
                        "warning",
                        "won_deal_reopened",
                        "This deal has a draft project awaiting confirmation but is no longer "
                        "Closed Won in HubSpot.",
                        deal.hubspot_deal_id,
                    )
                )

    # --- escalation: drafts unconfirmed past their due date (owner and CRO are alerted in-app)
    overdue = await session.scalars(
        select(DraftConfirmation).where(
            DraftConfirmation.confirmed_at.is_(None),
            DraftConfirmation.escalated_at.is_(None),
            DraftConfirmation.due_at <= now,
        )
    )
    for confirmation in overdue:
        confirmation.escalated_at = now
        session.add(
            PipelineEvent(
                event_type="draft_overdue",
                opportunity_id=confirmation.opportunity_id,
                project_id=confirmation.project_id,
                sync_run_id=run.id,
                actor_label=SYNC_ACTOR,
                message=(
                    "Draft not confirmed within "
                    f"{settings.draft_confirmation_business_days} business days; the owner "
                    "and Sales leadership are alerted"
                ),
            )
        )

    ids = {d.hubspot_deal_id: d.id for d in refreshed.values()}
    for finding in findings:
        session.add(
            HubspotSyncFinding(
                run_id=run.id,
                opportunity_id=ids.get(finding.deal_id or ""),
                hubspot_deal_id=finding.deal_id,
                severity=finding.severity,
                code=finding.code,
                message=finding.message,
                details=finding.details,
            )
        )
    report.findings = findings
    run.deals_seen = report.deals_seen
    run.deals_created, run.deals_updated = report.deals_created, report.deals_updated
    run.deals_unchanged, run.drafts_created = report.deals_unchanged, report.drafts_created
    run.lines_retired, run.findings_count = report.lines_retired, len(findings)
    await session.flush()


def _resolve_owner(
    source_deal: SourceDeal, by_name: dict[str, list[User]]
) -> tuple[User | None, list[SourceFinding]]:
    """Match the HubSpot deal owner to a known person by name (case and spacing ignored)."""
    if not source_deal.owner_name:
        return None, [
            SourceFinding("warning", "missing_owner", "No deal owner.", source_deal.deal_id)
        ]
    matches = by_name.get(_norm(source_deal.owner_name), [])
    if len(matches) == 1:
        return matches[0], []
    code = "owner_ambiguous" if matches else "owner_unresolved"
    return None, [
        SourceFinding(
            "warning",
            code,
            f"Deal owner '{source_deal.owner_name}' does not match exactly one known person.",
            source_deal.deal_id,
        )
    ]


def _record_transition(
    event: Any,
    deal: HubspotOpportunity,
    old_lifecycle: str | None,
    old_stage: str | None,
    report: SyncReport,
) -> None:
    new = deal.lifecycle
    contributed = old_lifecycle in CONTRIBUTING
    if new == "closed_lost" and old_lifecycle not in (None, "closed_lost"):
        event(
            "deal_closed_lost",
            deal,
            "Closed Lost in HubSpot: weighted line removed"
            if contributed
            else "Closed Lost in HubSpot",
            old_lifecycle,
            new,
        )
        report.lines_retired += int(contributed)
    elif new == "excluded" and contributed:
        event(
            "deal_removed_from_forecast",
            deal,
            f"Moved below the forecast stage ({deal.stage}): weighted line removed",
            old_lifecycle,
            new,
        )
        report.lines_retired += 1
    elif new == "closed_won" and old_lifecycle != "closed_won":
        event("deal_closed_won", deal, "Closed Won in HubSpot", old_lifecycle, new)
    elif new == "weighted" and old_lifecycle != "weighted":
        event(
            "deal_entered_forecast",
            deal,
            f"Entered the forecast at {deal.stage}",
            old_lifecycle,
            new,
        )
    elif new == "weighted" and old_stage != deal.stage:
        event("stage_changed", deal, "Stage changed", old_stage, deal.stage)


def _quality_findings(deals: list[HubspotOpportunity], as_of: date) -> list[SourceFinding]:
    out: list[SourceFinding] = []
    groups: dict[tuple[Any, ...], list[HubspotOpportunity]] = {}
    for deal in deals:
        if deal.lifecycle != "weighted":
            continue
        if deal.close_date is not None and deal.close_date.date() < as_of:
            out.append(
                SourceFinding(
                    "warning",
                    "stale_close_date",
                    f"Open deal with a close date in the past ({deal.close_date:%Y-%m-%d}); "
                    "ask the owner to update HubSpot.",
                    deal.hubspot_deal_id,
                )
            )
        key = (
            deal.company_hubspot_id or _norm(deal.company_resolved_name),
            deal.amount,
            deal.stage,
            deal.close_date.date() if deal.close_date else None,  # same day, whatever the time
        )
        groups.setdefault(key, []).append(deal)
    for key, group in groups.items():
        if len(group) > 1 and key[0]:
            for deal in group:
                others = ", ".join(d.hubspot_deal_id for d in group if d is not deal)
                out.append(
                    SourceFinding(
                        "warning",
                        "possible_duplicate_deal",
                        f"Same company, amount, stage and close date as deal(s) {others}: possibly "
                        "the same opportunity entered twice. Both are weighted until resolved.",
                        deal.hubspot_deal_id,
                    )
                )
    return out
