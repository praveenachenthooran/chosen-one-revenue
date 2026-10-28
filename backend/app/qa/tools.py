"""Read-only tools for the AI Revenue Detective.

Each tool is typed (a pydantic model validates its arguments), only reads, and runs *as the person
who asked for the investigation*, so the agent can see nothing that person could not: a project's
change history is only returned to someone allowed to view it. Every result carries evidence
references (`project:12`, `revision:345`, `snapshot:3`, `deal:7`) so a claim can be traced to the
record it came from. There is no tool that writes, edits, accepts or dismisses anything.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.permissions import can_view_history
from app.models import (
    ForecastSnapshot,
    ForecastSnapshotLine,
    HubspotOpportunity,
    PipelineEvent,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    RevenueRevision,
    User,
)
from app.qa.rules import _normalise, j  # pyright: ignore[reportPrivateUsage]

MAX_ROWS = 40


@dataclass
class ToolContext:
    session: AsyncSession
    user: User
    refs: set[str] = field(default_factory=lambda: set())  # every reference returned so far
    steps: list[dict[str, Any]] = field(default_factory=lambda: [])  # what was looked at


class ToolResult(BaseModel):
    data: dict[str, Any]
    refs: list[str] = Field(default_factory=lambda: [])


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectArgs(_Args):
    project_id: int = Field(description="The Chosen One project id")


class HistoryArgs(ProjectArgs):
    limit: int = Field(default=30, ge=1, le=MAX_ROWS)


class CompareArgs(ProjectArgs):
    snapshot_a: int | None = Field(
        default=None, description="Older snapshot id (default: second newest)"
    )
    snapshot_b: int | None = Field(default=None, description="Newer snapshot id (default: newest)")


class DealArgs(_Args):
    opportunity_id: int | None = Field(
        default=None, description="The HubSpot deal's id in this app"
    )
    company: str | None = Field(
        default=None, description="List deals for this company name instead"
    )


def _denied(what: str) -> ToolResult:
    return ToolResult(
        data={"error": "not_authorized", "message": f"You are not allowed to see {what}."}
    )


def _not_found(what: str) -> ToolResult:
    return ToolResult(data={"error": "not_found", "message": f"{what} not found."})


async def _project(session: AsyncSession, project_id: int) -> Project | None:
    return await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.customer),
            selectinload(Project.owners).selectinload(ProjectOwner.user),
        )
    )


async def get_project_history(ctx: ToolContext, args: HistoryArgs) -> ToolResult:
    """The audit history of a project's revenue: who changed which month, when, from and to, and why."""
    project = await _project(ctx.session, args.project_id)
    if project is None:
        return _not_found("Project")
    if not can_view_history(ctx.user, project):
        return _denied("this project's change history")
    rows = await ctx.session.execute(
        select(RevenueRevision, User.name)
        .outerjoin(User, User.id == RevenueRevision.actor_user_id)
        .where(RevenueRevision.project_id == project.id)
        .order_by(RevenueRevision.created_at.desc(), RevenueRevision.id.desc())
        .limit(args.limit)
    )
    items: list[dict[str, Any]] = [
        {
            "ref": f"revision:{r.id}", "when": r.created_at, "month": r.month, "field": r.field,
            "change": r.change_type, "from": r.old_value, "to": r.new_value,
            "by": name or r.actor_label, "reason_code": r.reason_code, "note": r.note,
            "from_import": r.actor_label == "excel-import",
        }
        for r, name in rows
    ]  # fmt: skip
    total = await ctx.session.scalar(
        select(func.count())
        .select_from(RevenueRevision)
        .where(RevenueRevision.project_id == project.id)
    )
    return ToolResult(
        data=j(
            {
                "project": f"{project.customer.name} - {project.name}",
                "total_events": total,
                "events": items,
            }
        ),
        refs=[f"project:{project.id}", *[str(i["ref"]) for i in items]],
    )


async def compare_snapshots(ctx: ToolContext, args: CompareArgs) -> ToolResult:
    """One project's committed revenue by month in two saved snapshots, and what changed between them."""
    project = await _project(ctx.session, args.project_id)
    if project is None:
        return _not_found("Project")
    wanted = [s for s in (args.snapshot_a, args.snapshot_b) if s is not None]
    if wanted:
        snaps = list(
            await ctx.session.scalars(
                select(ForecastSnapshot).where(ForecastSnapshot.id.in_(wanted))
            )
        )
    else:
        snaps = list(
            await ctx.session.scalars(
                select(ForecastSnapshot).order_by(ForecastSnapshot.taken_at.desc()).limit(2)
            )
        )
    snaps.sort(key=lambda s: s.taken_at)
    if len(snaps) < 2:
        return ToolResult(
            data={"available": False, "message": "At least two snapshots are needed to compare."},
            refs=[f"project:{project.id}"],
        )
    older, newer = snaps[0], snaps[-1]
    series: dict[int, dict[date, Decimal]] = {}
    for snap in (older, newer):
        rows = await ctx.session.execute(
            select(ForecastSnapshotLine.month, func.sum(ForecastSnapshotLine.amount))
            .where(
                ForecastSnapshotLine.snapshot_id == snap.id,
                ForecastSnapshotLine.line_type == "committed",
                ForecastSnapshotLine.project_id == project.id,
            )
            .group_by(ForecastSnapshotLine.month)
        )
        series[snap.id] = {m: Decimal(a) for m, a in rows}
    months = sorted(set(series[older.id]) | set(series[newer.id]))
    zero = Decimal("0.00")
    changes = [
        {"month": m, "older": series[older.id].get(m, zero), "newer": series[newer.id].get(m, zero),
         "difference": series[newer.id].get(m, zero) - series[older.id].get(m, zero)}
        for m in months
    ]  # fmt: skip
    return ToolResult(
        data=j(
            {
                "available": True,
                "older": {
                    "ref": f"snapshot:{older.id}",
                    "label": older.label,
                    "taken_at": older.taken_at,
                },
                "newer": {
                    "ref": f"snapshot:{newer.id}",
                    "label": newer.label,
                    "taken_at": newer.taken_at,
                },
                "months": changes,
                "changed_months": [c["month"] for c in changes if c["difference"] != 0],
            }
        ),
        refs=[f"project:{project.id}", f"snapshot:{older.id}", f"snapshot:{newer.id}"],
    )


async def get_hubspot_deals(ctx: ToolContext, args: DealArgs) -> ToolResult:
    """HubSpot deals (read-only mirror): one deal with its lifecycle events, or a company's deals."""
    if args.opportunity_id is None and not args.company:
        return ToolResult(
            data={"error": "bad_request", "message": "Give opportunity_id or company."}
        )
    stmt = select(HubspotOpportunity).options(selectinload(HubspotOpportunity.link))
    if args.opportunity_id is not None:
        stmt = stmt.where(HubspotOpportunity.id == args.opportunity_id)
    else:
        stmt = stmt.where(
            func.lower(HubspotOpportunity.company_resolved_name)
            == (args.company or "").strip().lower()
        )
    deals = list(await ctx.session.scalars(stmt.order_by(HubspotOpportunity.id).limit(MAX_ROWS)))
    if not deals:
        return _not_found("Deal")
    out: list[dict[str, Any]] = []
    refs: list[str] = []
    for deal in deals:
        events = list(
            await ctx.session.scalars(
                select(PipelineEvent)
                .where(PipelineEvent.opportunity_id == deal.id)
                .order_by(PipelineEvent.id.desc())
                .limit(10)
            )
        )
        refs.append(f"deal:{deal.id}")
        out.append(
            {
                "ref": f"deal:{deal.id}", "hubspot_deal_id": deal.hubspot_deal_id, "name": deal.name,
                "company": deal.company_resolved_name, "stage": deal.stage, "lifecycle": deal.lifecycle,
                "amount": deal.amount, "close_date": deal.close_date,
                "expected_start": deal.expected_start, "duration_months": deal.duration_months,
                "owner": deal.owner_name,
                "linked_project": f"project:{deal.link.project_id}" if deal.link else None,
                "events": [
                    {"ref": f"pipeline_event:{e.id}", "when": e.created_at, "type": e.event_type, "message": e.message}
                    for e in events
                ],
            }
        )  # fmt: skip
        if deal.link:
            refs.append(f"project:{deal.link.project_id}")
    return ToolResult(data=j({"deals": out}), refs=refs)


async def find_duplicates(ctx: ToolContext, args: ProjectArgs) -> ToolResult:
    """Other projects for the same customer whose name and forward revenue resemble this one."""
    project = await _project(ctx.session, args.project_id)
    if project is None:
        return _not_found("Project")
    peers = list(
        await ctx.session.scalars(
            select(Project)
            .where(Project.customer_id == project.customer_id, Project.id != project.id)
            .options(selectinload(Project.customer))
        )
    )

    async def series(project_id: int) -> dict[date, Decimal]:
        rows = await ctx.session.execute(
            select(ProjectMonthlyRevenue.month, ProjectMonthlyRevenue.amount).where(
                ProjectMonthlyRevenue.project_id == project_id
            )
        )
        return {m: a for m, a in rows}

    mine = await series(project.id)
    out: list[dict[str, Any]] = []
    for peer in peers:
        ratio = SequenceMatcher(None, _normalise(project.name), _normalise(peer.name)).ratio()
        theirs = await series(peer.id)
        shared = [m for m in mine if mine[m] > 0 and theirs.get(m, Decimal(0)) > 0]
        equal = [m for m in shared if mine[m] == theirs[m]]
        if ratio >= 0.8 or equal:
            out.append(
                {"ref": f"project:{peer.id}", "row_id": peer.source_row_id, "name": peer.name,
                 "status": peer.status, "created_at": peer.created_at, "name_similarity": round(ratio, 3),
                 "shared_months": len(shared), "equal_months": len(equal)}
            )  # fmt: skip
    return ToolResult(
        data=j({"project": f"project:{project.id}", "candidates": out}),
        refs=[f"project:{project.id}", *[c["ref"] for c in out]],
    )


async def get_project_owners(ctx: ToolContext, args: ProjectArgs) -> ToolResult:
    """Who owns a project (Client Partner, AE, Commercial Owner), so a finding goes to the right person."""
    project = await _project(ctx.session, args.project_id)
    if project is None:
        return _not_found("Project")
    owners = [{"role": o.role, "name": o.user.name, "code": o.user.code} for o in project.owners]
    return ToolResult(
        data={"project": f"{project.customer.name} - {project.name}", "owners": owners,
              "has_owner": bool(owners)},
        refs=[f"project:{project.id}"],
    )  # fmt: skip


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[ToolContext, Any], Awaitable[ToolResult]]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_project_history",
            get_project_history.__doc__ or "",
            HistoryArgs,
            get_project_history,
        ),
        Tool("compare_snapshots", compare_snapshots.__doc__ or "", CompareArgs, compare_snapshots),
        Tool("get_hubspot_deals", get_hubspot_deals.__doc__ or "", DealArgs, get_hubspot_deals),
        Tool("find_duplicates", find_duplicates.__doc__ or "", ProjectArgs, find_duplicates),
        Tool(
            "get_project_owners", get_project_owners.__doc__ or "", ProjectArgs, get_project_owners
        ),
    )
}


async def call_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> ToolResult:
    """Validate and run one tool, recording the step. Unknown tools and bad arguments are returned
    to the caller as errors, never raised, so a confused model cannot break the run."""
    tool = TOOLS.get(name)
    if tool is None:
        result = ToolResult(data={"error": "unknown_tool", "message": f"No tool named {name}."})
    else:
        try:
            args = tool.args_model.model_validate(raw_args)
            result = await tool.run(ctx, args)
        except ValidationError as exc:
            result = ToolResult(data={"error": "bad_arguments", "message": str(exc)[:400]})
    ctx.refs.update(result.refs)
    ctx.steps.append(
        {
            "tool": name, "arguments": raw_args, "refs": result.refs,
            "outcome": result.data.get("error", "ok"),
        }
    )  # fmt: skip
    return result
