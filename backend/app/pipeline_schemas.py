"""Response and request models for the pipeline API (money as exact decimal strings)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageOut(Out):
    id: int
    name: str
    probability: Decimal
    kind: str
    in_forecast: bool
    position: int
    source: str


class StageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probability: object = None
    in_forecast: object = None


class LinkedProject(Out):
    id: int
    source_row_id: str
    name: str
    status: str


class DraftInfo(Out):
    project_id: int
    owner_name: str | None
    requested_at: datetime
    due_at: datetime
    overdue: bool
    confirmed_at: datetime | None
    possible_duplicates: list[dict[str, Any]]


class DealSummary(Out):
    id: int
    hubspot_deal_id: str
    name: str
    company: str | None
    company_id: str | None
    owner_name: str | None
    owner_code: str | None
    stage: str
    probability: Decimal | None
    amount: Decimal | None
    weighted_amount: Decimal
    expected_start: date | None
    duration_months: int | None
    close_date: datetime | None
    practice: str | None
    region: str | None
    industry: str | None
    lifecycle: str
    status: str
    counts_in_forecast: bool
    phaseable: bool
    unphased_reason: str | None
    linked_project: LinkedProject | None
    draft: DraftInfo | None
    findings: int


class DealPage(Out):
    items: list[DealSummary]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    weighted_total: Decimal


class MonthAmount(Out):
    month: date
    amount: Decimal


class FindingOut(Out):
    severity: str
    code: str
    message: str


class EventOut(Out):
    id: int
    event_type: str
    created_at: datetime
    actor_label: str
    from_value: str | None
    to_value: str | None
    message: str


class ScheduleCheck(Out):
    deal_amount: Decimal | None
    schedule_total: Decimal
    outside_window: Decimal
    variance_ratio: Decimal | None
    exceeds_threshold: bool


class DealDetail(DealSummary):
    monthly: list[MonthAmount]
    outside_window: Decimal
    findings_list: list[FindingOut]
    events: list[EventOut]
    schedule_check: ScheduleCheck | None
    can_confirm: bool
    raw: dict[str, Any] | None


class ForecastMonth(Out):
    month: date
    fiscal_year: str
    fiscal_period: int
    committed: Decimal
    weighted: Decimal
    total: Decimal


class ForecastYear(Out):
    fiscal_year: str
    committed: Decimal
    weighted: Decimal
    total: Decimal


class Unphased(Out):
    deals: int
    weighted_amount: Decimal


class ForecastTotals(Out):
    months: list[ForecastMonth]
    fiscal_years: list[ForecastYear]
    committed_total: Decimal
    weighted_total: Decimal
    combined_total: Decimal
    weighted_deals: int
    unphased: Unphased
    outside_window_weighted: Decimal
    filters_applied: bool


class SyncRunOut(Out):
    id: int
    status: str
    source: str
    source_ref: str | None
    started_at: datetime
    finished_at: datetime | None
    deals_seen: int
    deals_created: int
    deals_updated: int
    deals_unchanged: int
    drafts_created: int
    lines_retired: int
    findings_count: int
    error_message: str | None
    triggered_by_label: str


class SyncStatus(Out):
    last_run: SyncRunOut | None
    last_success: SyncRunOut | None
    stale: bool
    reason: str | None  # never | failed | old
    stale_since: datetime | None
    stale_after_minutes: int
    can_sync: bool
    configured_source: str


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_possible_duplicate: StrictBool = False
    category: str | None = None
    industry: str | None = None


class ConfirmResult(Out):
    project_id: int
    status: str
    confirmed_at: datetime
    weighted_line_retired: bool
    schedule_check: ScheduleCheck | None


class ConfirmationItem(Out):
    project_id: int
    project_name: str
    customer: str
    deal_id: int
    hubspot_deal_id: str
    owner_name: str | None
    amount: Decimal | None
    schedule_total: Decimal
    requested_at: datetime
    due_at: datetime
    overdue: bool
    has_possible_duplicates: bool
    can_confirm: bool


class Person(Out):
    code: str
    name: str


class FilterOptions(Out):
    practices: list[str]
    regions: list[str]
    industries: list[str]
    categories: list[str]
    customers: list[str]
    owners: list[Person]
