"""Deterministic QA rules (PRD FR-35 onwards): the same data always gives the same findings.

Every rule is a pure function of `QaData`, a read-only picture of the forecast. A rule produces
`Detected` findings with evidence and a deterministic financial impact. Nothing here uses AI and
nothing here changes data; the AI Revenue Detective only *explains* what these rules found.

Financial impact, by rule (documented in docs/milestone-5-notes.md):
  duplicate_project      forward revenue counted twice: the smaller of the two rows, summed over open months
  closed_won_missing     the deal amount that is not yet in the committed forecast
  amount_mismatch        the absolute gap between the deal amount and the committed schedule
  stale_row              committed revenue in the 3-month window that has not been reviewed
  mom_anomaly            the size of the largest month-over-month movement
  missing_required       forward committed revenue on the incomplete row
  deal_missing_fields    the deal's weighted amount that cannot be phased into months
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import Any, cast

from app.config import Settings
from app.models import DraftConfirmation, Project, RevenueRevision, User
from app.pipeline.drafts import DRAFT_STATUS
from app.pipeline.forecast import DealView
from app.pipeline.weighting import add_months, month_start, phase

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

DUPLICATE, CLOSED_WON, MISMATCH, STALE, ANOMALY, MISSING, DEAL_MISSING = (
    "duplicate_project",
    "closed_won_missing",
    "amount_mismatch",
    "stale_row",
    "mom_anomaly",
    "missing_required",
    "deal_missing_fields",
)
RULES: dict[str, str] = {
    DUPLICATE: "Possible duplicate project",
    CLOSED_WON: "Closed Won deal not in the committed forecast",
    MISMATCH: "HubSpot amount differs from the committed schedule",
    STALE: "Row in the 3-month window not updated recently",
    ANOMALY: "Unusual month-over-month revenue movement",
    MISSING: "Missing required fields",
    DEAL_MISSING: "HubSpot deal missing phasing fields",
}
OWNER_ORDER = ("client_partner", "commercial_owner", "ae")  # who a row's findings go to


@dataclass
class Detected:
    rule_code: str
    key: str  # what identifies the subject, e.g. a Row ID or Deal ID
    severity: str
    title: str
    summary: str
    evidence: dict[str, Any]
    suggested_fix: str
    impact: Decimal
    project_id: int | None = None
    opportunity_id: int | None = None
    related_project_ids: list[int] = field(default_factory=lambda: [])
    route_to: int | None = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.rule_code}:{self.key}".encode()).hexdigest()


@dataclass
class QaData:
    now: datetime
    as_of: date | None  # the forecast's "as of" date (first open month)
    open_months: list[date]
    projects: list[Project]  # with customer and owners (and owner users) loaded
    revenue: dict[int, dict[date, Decimal]]  # project id -> month -> amount
    # latest human edit per project and month, and the latest human edit per project
    reasons: dict[tuple[int, date], RevenueRevision]
    last_human_edit: dict[int, datetime]
    views: list[DealView]
    confirmations: dict[int, DraftConfirmation]  # by opportunity id
    users: dict[int, User]
    settings: Settings


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def j(value: Any) -> Any:
    """JSON-safe copy: decimals as exact strings, dates as ISO text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): j(v) for k, v in cast("dict[Any, Any]", value).items()}
    if isinstance(value, list | tuple):
        return [j(v) for v in cast("list[Any]", value)]
    return value


def route_for_project(project: Project) -> int | None:
    by_role = {o.role: o.user_id for o in project.owners}
    return next((by_role[r] for r in OWNER_ORDER if r in by_role), None)


def _owners(project: Project) -> list[dict[str, str]]:
    return [
        {"role": o.role, "name": o.user.name, "code": o.user.code or ""} for o in project.owners
    ]


def _project_ref(project: Project) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "row_id": project.source_row_id,
        "customer": project.customer.name,
        "name": project.name,
        "status": project.status,
        "owners": _owners(project),
    }


def _committed(data: QaData) -> list[Project]:
    return [p for p in data.projects if p.status != DRAFT_STATUS]


def _active(data: QaData) -> list[Project]:
    return [p for p in data.projects if p.status == "Active"]


def _window(data: QaData) -> list[date]:
    """The 3-month window: this month and the next two."""
    start = month_start(data.now.date())
    return [add_months(start, i) for i in range(3)]


# ---- duplicates --------------------------------------------------------------------------------


def _normalise(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def duplicate_projects(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    by_customer: dict[int, list[Project]] = {}
    for project in _committed(data):
        by_customer.setdefault(project.customer_id, []).append(project)
    open_months = set(data.open_months)
    for group in by_customer.values():
        ordered = sorted(group, key=lambda p: p.id)
        for i, first in enumerate(ordered):
            for second in ordered[i + 1 :]:
                a, b = _normalise(first.name), _normalise(second.name)
                ratio = SequenceMatcher(None, a, b).ratio()
                rev_a, rev_b = data.revenue.get(first.id, {}), data.revenue.get(second.id, {})
                shared = [
                    m for m in open_months if rev_a.get(m, ZERO) > 0 and rev_b.get(m, ZERO) > 0
                ]
                equal = [m for m in shared if rev_a[m] == rev_b[m]]
                same_name = a == b
                similar_values = bool(shared) and len(equal) * 2 >= len(shared)
                if not (same_name or (ratio >= 0.92 and similar_values)):
                    continue
                impact = money(sum((min(rev_a[m], rev_b[m]) for m in shared), ZERO))
                found.append(
                    Detected(
                        DUPLICATE,
                        f"{first.source_row_id}+{second.source_row_id}",
                        "error" if same_name and similar_values else "warning",
                        f"Possible duplicate: {first.customer.name} - {first.name} / {second.name}",
                        f"{first.source_row_id} and {second.source_row_id} are for the same customer "
                        f"with {'the same name' if same_name else 'near-identical names'}"
                        + (
                            f" and equal revenue in {len(equal)} of {len(shared)} shared forward months"
                            if shared
                            else ""
                        )
                        + f". Up to {impact} of forward revenue may be counted twice.",
                        {
                            "rows": [_project_ref(first), _project_ref(second)],
                            "name_similarity": round(ratio, 3),
                            "shared_forward_months": len(shared),
                            "equal_forward_months": len(equal),
                            "created": {
                                first.source_row_id: first.created_at,
                                second.source_row_id: second.created_at,
                            },
                        },
                        "Confirm whether both rows are real. If one is a copy, move any needed "
                        "detail into the original and remove the copy's revenue (Sales leadership "
                        "or an owner edits it; this check never changes data).",
                        impact,
                        project_id=second.id,
                        related_project_ids=[first.id, second.id],
                        route_to=route_for_project(second),
                    )
                )
    return found


# ---- Closed Won deals not committed -------------------------------------------------------------


def closed_won_missing(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    for view in data.views:
        deal = view.deal
        if deal.lifecycle != "closed_won":
            continue
        project = view.linked_project
        if project is not None and project.status != DRAFT_STATUS:
            continue  # represented by a committed project
        confirmation = view.confirmation
        overdue = bool(
            confirmation and not confirmation.confirmed_at and confirmation.due_at < data.now
        )
        amount = money(deal.amount or ZERO)
        if project is None:
            reason = "no draft project could be created (see the deal's sync findings)"
        else:
            reason = f"its draft project {project.source_row_id} has not been confirmed" + (
                " and the confirmation is overdue" if overdue else ""
            )
        found.append(
            Detected(
                CLOSED_WON,
                deal.hubspot_deal_id,
                "error" if overdue or project is None else "warning",
                f"Closed Won not committed: {deal.name}",
                f"Deal {deal.hubspot_deal_id} is Closed Won ({amount}) but {reason}, so it is "
                "not in the committed forecast.",
                {
                    "deal": {
                        "id": deal.id, "hubspot_deal_id": deal.hubspot_deal_id, "name": deal.name,
                        "company": view.company, "stage": deal.stage, "amount": deal.amount,
                        "expected_start": deal.expected_start, "duration_months": deal.duration_months,
                        "owner": deal.owner_name,
                    },
                    "draft_project": _project_ref(project) if project else None,
                    "confirmation_due": confirmation.due_at if confirmation else None,
                    "overdue": overdue,
                },
                "The deal owner confirms the draft project's monthly schedule so committed "
                "revenue replaces the pipeline line.",
                amount,
                project_id=project.id if project else None,
                opportunity_id=deal.id,
                route_to=deal.owner_user_id,
            )
        )  # fmt: skip
    return found


# ---- HubSpot amount versus the committed schedule ----------------------------------------------


def amount_mismatch(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    threshold = data.settings.qa_mismatch_percent / 100
    months = sorted({m for rev in data.revenue.values() for m in rev} | set(data.open_months))
    for view in data.views:
        deal, project = view.deal, view.linked_project
        if project is None or project.status == DRAFT_STATUS or not deal.amount:
            continue
        total = sum(data.revenue.get(project.id, {}).values(), ZERO)
        outside = ZERO
        phasing = phase(deal.amount, Decimal(1), deal.expected_start, deal.duration_months, months)
        if phasing.phaseable:
            outside = phasing.outside_window  # part of the deal falls beyond the forecast months
        expected = deal.amount - outside
        gap = abs(total - expected)
        ratio = gap / deal.amount
        if ratio <= threshold:
            continue
        found.append(
            Detected(
                MISMATCH,
                deal.hubspot_deal_id,
                "warning",
                f"HubSpot amount differs from schedule: {deal.name}",
                f"Deal {deal.hubspot_deal_id} is {money(deal.amount)} in HubSpot but "
                f"{project.source_row_id}'s committed schedule totals {money(total)} "
                f"(expected about {money(expected)} within the forecast months): "
                f"{(ratio * 100).quantize(Decimal('0.1'))}% apart, more than "
                f"{data.settings.qa_mismatch_percent.normalize():f}%.",
                {
                    "deal": {"id": deal.id, "hubspot_deal_id": deal.hubspot_deal_id,
                             "name": deal.name, "amount": deal.amount, "stage": deal.stage},
                    "project": _project_ref(project),
                    "schedule_total": total,
                    "deal_amount_outside_forecast_months": outside,
                    "expected_in_forecast_months": expected,
                    "difference_percent": (ratio * 100).quantize(Decimal("0.01")),
                },
                "Check which is right. If HubSpot changed, update the committed schedule; if the "
                "schedule changed on purpose, ask Sales Ops to align the HubSpot amount.",
                money(gap),
                project_id=project.id,
                opportunity_id=deal.id,
                route_to=route_for_project(project),
            )
        )  # fmt: skip
    return found


# ---- stale rows --------------------------------------------------------------------------------


def stale_rows(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    window = _window(data)
    today = data.now.date()
    for project in _active(data):
        revenue = data.revenue.get(project.id, {})
        in_window = {m: revenue[m] for m in window if revenue.get(m, ZERO) > 0}
        if not in_window:
            continue
        candidates: list[tuple[date, str]] = []
        if project.source_last_updated:
            candidates.append((project.source_last_updated, "last updated in the source workbook"))
        if project.id in data.last_human_edit:
            candidates.append((data.last_human_edit[project.id].date(), "last edited in the app"))
        last, basis = max(candidates) if candidates else (project.created_at.date(), "created")
        days = (today - last).days
        if days <= data.settings.qa_stale_days:
            continue
        impact = money(sum(in_window.values(), ZERO))
        found.append(
            Detected(
                STALE,
                project.source_row_id,
                "warning",
                f"Not updated for {days} days: {project.customer.name} - {project.name}",
                f"{project.source_row_id} has {impact} of revenue in the next three months but was "
                f"{basis} on {last.isoformat()}, {days} days ago (limit "
                f"{data.settings.qa_stale_days}).",
                {
                    "project": _project_ref(project),
                    "last_updated": last,
                    "basis": basis,
                    "days_since_update": days,
                    "window_revenue": in_window,
                },
                "The owner reviews the row, adjusts the months if needed and records the "
                "update (editing a month, even to the same plan, records the review).",
                impact,
                project_id=project.id,
                route_to=route_for_project(project),
            )
        )  # fmt: skip
    return found


# ---- month-over-month anomalies --------------------------------------------------------------


def mom_anomalies(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    open_set = set(data.open_months)
    min_amount = data.settings.qa_anomaly_min_amount
    min_ratio = data.settings.qa_anomaly_min_percent / 100
    for project in _active(data):
        if "fixed" in (project.billing_type or "").lower():
            continue  # milestone billing is lumpy by nature
        revenue = data.revenue.get(project.id, {})
        months = sorted(revenue)
        moves: list[dict[str, Any]] = []
        for previous, current in zip(months, months[1:], strict=False):
            if current not in open_set or add_months(previous, 1) != current:
                continue
            before, after = revenue[previous], revenue[current]
            delta = after - before
            if before <= 0 or abs(delta) < min_amount or abs(delta) / before < min_ratio:
                continue
            if (project.id, current) in data.reasons and data.reasons[
                (project.id, current)
            ].reason_code:
                continue  # already explained with a reason code
            moves.append(
                {
                    "month": current,
                    "previous_month": previous,
                    "before": before,
                    "after": after,
                    "change": delta,
                    "change_percent": (delta / before * 100).quantize(Decimal("0.1")),
                    "reason_recorded": False,
                }
            )
        if not moves:
            continue
        biggest = max(moves, key=lambda m: abs(m["change"]))
        direction = "rise" if biggest["change"] > 0 else "drop"
        found.append(
            Detected(
                ANOMALY,
                project.source_row_id,
                "warning",
                f"Unusual revenue {direction}: {project.customer.name} - {project.name}",
                f"{project.source_row_id} moves {money(biggest['before'])} to {money(biggest['after'])} "
                f"in {biggest['month']:%b %Y} ({biggest['change_percent']}%) with no reason recorded"
                + (f"; {len(moves)} movements in total." if len(moves) > 1 else "."),
                {"project": _project_ref(project), "movements": moves,
                 "billing_type": project.billing_type},
                "Ask the owner what changed. If it is intended, record the reason when editing the "
                "month; if not, correct it.",
                money(abs(biggest["change"])),
                project_id=project.id,
                route_to=route_for_project(project),
            )
        )  # fmt: skip
    return found


# ---- missing required fields -------------------------------------------------------------------


def missing_required(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    open_set = set(data.open_months)
    for project in _committed(data):
        if project.status == "Completed":
            continue
        missing: list[str] = []
        if not project.owners:
            missing.append("owner (Client Partner, AE or Commercial Owner)")
        for label, value in (
            ("category (EE/EN/NN)", project.category_code),
            ("TECH / practice", project.practice),
            ("region", project.region),
            ("industry", project.industry),
        ):
            if not value:
                missing.append(label)
        if not missing:
            continue
        forward = money(
            sum((a for m, a in data.revenue.get(project.id, {}).items() if m in open_set), ZERO)
        )
        found.append(
            Detected(
                MISSING,
                project.source_row_id,
                "error" if not project.owners else "warning",
                f"Missing {', '.join(m.split(' (')[0] for m in missing)}: "
                f"{project.customer.name} - {project.name}",
                f"{project.source_row_id} is missing: {', '.join(missing)}. It has {forward} of forward "
                "revenue.",
                {"project": _project_ref(project), "missing": missing},
                "Complete the missing fields. Only Sales leadership or an Administrator can "
                "assign an owner to a row that has none."
                if not project.owners
                else "The owner completes the missing fields.",
                forward,
                project_id=project.id,
                route_to=route_for_project(project),
            )
        )  # fmt: skip
    return found


def deal_missing_fields(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    for view in data.views:
        if not view.counts or view.phasing is None or view.phasing.phaseable:
            continue
        deal = view.deal
        missing = [
            label
            for label, value in (
                ("amount", deal.amount),
                ("expected start date", deal.expected_start),
                ("duration", deal.duration_months),
            )
            if not value
        ]
        found.append(
            Detected(
                DEAL_MISSING,
                deal.hubspot_deal_id,
                "warning",
                f"HubSpot deal cannot be phased: {deal.name}",
                f"Deal {deal.hubspot_deal_id} has no {', '.join(missing) or 'valid phasing fields'}, so "
                "its weighted amount is not in the monthly figures.",
                {"deal": {"id": deal.id, "hubspot_deal_id": deal.hubspot_deal_id, "name": deal.name,
                          "stage": deal.stage, "amount": deal.amount},
                 "missing": missing, "reason": view.phasing.reason,
                 "weighted_amount": view.phasing.weighted_amount},
                "Ask the deal owner to complete the fields in HubSpot (this app never writes to it).",
                money(view.phasing.weighted_amount),
                opportunity_id=deal.id,
                route_to=deal.owner_user_id,
            )
        )  # fmt: skip
    return found


ALL_RULES = (
    duplicate_projects,
    closed_won_missing,
    amount_mismatch,
    stale_rows,
    mom_anomalies,
    missing_required,
    deal_missing_fields,
)


def detect(data: QaData) -> list[Detected]:
    found: list[Detected] = []
    for rule in ALL_RULES:
        found.extend(rule(data))
    return found
