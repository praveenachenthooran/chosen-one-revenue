"""Response and request models for the QA API (money as exact decimal strings)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FindingOut(Out):
    id: int
    rule_code: str
    rule_label: str
    severity: str
    title: str
    summary: str
    status: str
    financial_impact: Decimal
    routed_to_id: int | None
    routed_to_name: str | None
    project_id: int | None
    opportunity_id: int | None
    related_project_ids: list[int]
    created_at: datetime
    last_seen_at: datetime | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_reason: str | None
    investigation_status: str | None  # of the latest investigation, if any
    awaiting_review: bool  # open and investigated: a human decision is the next step
    can_review: bool


class ProjectRef(Out):
    project_id: int
    row_id: str
    customer: str
    name: str
    status: str


class FindingEventOut(Out):
    id: int
    event_type: str
    created_at: datetime
    actor_label: str
    message: str
    details: dict[str, Any]


class InvestigationOut(Out):
    id: int
    finding_id: int
    status: str  # investigating | completed | failed
    mode: str  # ai | deterministic
    model: str | None
    requested_by_label: str
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any] | None
    steps: list[dict[str, Any]]
    fallback_reason: str | None
    error_message: str | None


class FindingDetail(FindingOut):
    evidence: dict[str, Any]
    suggested_fix: str | None
    projects: list[ProjectRef]
    deal: dict[str, Any] | None
    events: list[FindingEventOut]
    investigations: list[InvestigationOut]
    can_investigate: bool
    ai_available: bool
    ai_unavailable_reason: str | None


class FindingPage(Out):
    items: list[FindingOut]
    total_items: int


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=2000)


class RunOut(Out):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    triggered_by_label: str
    findings_new: int
    findings_seen: int
    findings_reopened: int
    findings_resolved: int
    findings_open: int
    rule_counts: dict[str, int]
    error_message: str | None


class Metric(Out):
    key: str
    label: str
    value: str
    formula: str


class OwnerLoad(Out):
    user_id: int | None
    name: str
    open_findings: int
    revenue_at_stake: Decimal
    oldest_days: int


class HealthOut(Out):
    status: str  # green | amber | red
    status_reasons: list[str]
    open_total: int
    open_by_severity: dict[str, int]
    open_by_rule: dict[str, int]
    revenue_at_stake: Decimal
    unassigned_open: int
    awaiting_review: int
    owners: list[OwnerLoad]
    metrics: list[Metric]
    last_run: RunOut | None
    last_success: RunOut | None
    checks_overdue: bool
    as_of: date
    can_run: bool
