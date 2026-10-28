"""Response models for the read-only forecast API.

Money is serialized as exact decimal strings (e.g. "80950.00"), never floats.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OutModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AuthUser(OutModel):
    id: int
    name: str
    email: str | None
    roles: list[str]
    can_edit_all: bool


class AuthStatus(OutModel):
    authenticated: bool
    user: AuthUser | None = None
    csrf_token: str | None = None
    csrf_header: str | None = None


class OwnerOut(OutModel):
    role: str
    code: str | None
    name: str


class AttentionOut(OutModel):
    code: str
    message: str
    field: str | None = None


class ProjectSummary(OutModel):
    id: int
    source_row_id: str
    customer: str
    name: str
    status: str
    practice: str | None
    region: str | None
    industry: str | None
    country: str | None
    billing_type: str | None
    category: str | None
    client_partner: OwnerOut | None
    ae: OwnerOut | None
    commercial_owner: OwnerOut | None
    total_revenue: Decimal
    attention_count: int
    last_updated: date | None
    can_edit: bool = False
    monthly: dict[date, Decimal] | None = None


class ProjectPage(OutModel):
    items: list[ProjectSummary]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class MonthlyAmount(OutModel):
    month: date
    fiscal_year: str
    amount: Decimal
    # The value the Excel import loaded; editing never changes it. None = month added in the app.
    baseline_amount: Decimal | None = None
    edited: bool = False
    source_cell: str | None = None


class ProjectDetail(ProjectSummary):
    source_row_number: int | None
    last_updated_by: str | None
    owners: list[OwnerOut]
    monthly_revenue: list[MonthlyAmount]
    attention: list[AttentionOut]
    can_view_history: bool = False


class ProjectRevenue(OutModel):
    project_id: int
    months: list[MonthlyAmount]
    total: Decimal


class MonthColumn(OutModel):
    month: date
    label: str
    fiscal_year: str
    fiscal_period: int
    is_closed: bool


class MonthsResponse(OutModel):
    as_of: date | None
    months: list[MonthColumn]


class MonthlyTotal(OutModel):
    month: date
    fiscal_year: str
    fiscal_period: int
    total: Decimal
    project_count: int


class FiscalYearTotal(OutModel):
    fiscal_year: str
    total: Decimal
    months_with_data: int


class TotalsResponse(OutModel):
    basis: str = "current"
    months: list[MonthlyTotal]
    fiscal_years: list[FiscalYearTotal]
    grand_total: Decimal
    project_count: int


class ImportIssueOut(OutModel):
    severity: str
    code: str
    sheet: str
    row_number: int | None
    source_row_id: str | None
    field: str | None
    value: str | None
    message: str
    row_rejected: bool


class ReconciliationOut(OutModel):
    month: date
    fiscal_year: str
    source_total: Decimal
    database_total: Decimal
    excel_reported_total: Decimal | None
    variance: Decimal
    matches: bool


class ImportRunSummary(OutModel):
    id: int
    source_file: str
    source_sha256: str
    source_as_of: date | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_read: int
    projects_created: int
    projects_updated: int
    projects_unchanged: int
    rows_rejected: int
    warnings: int
    reconciled: bool | None


class ImportRunDetail(ImportRunSummary):
    issues: list[ImportIssueOut]
    reconciliation: list[ReconciliationOut]


class RevenueEditRequest(BaseModel):
    """Body for editing one month. Fields are deliberately untyped so that permission checks run
    before value validation; the handler validates them and answers with specific errors."""

    model_config = ConfigDict(extra="forbid")

    amount: object
    # What the editor last saw: a number, or null for a blank month. Required, so that a client
    # can never overwrite a value it has not seen.
    expected_amount: object
    reason: object = None


class RevenueEditResult(OutModel):
    project_id: int
    month: date
    amount: Decimal
    previous_amount: Decimal | None
    changed: bool
    project_total: Decimal
    revision_id: int | None


class HistoryItem(OutModel):
    id: int
    created_at: datetime
    field: str
    month: date | None
    change_type: str
    old_value: str | None
    new_value: str | None
    reason: str | None
    actor_name: str
    from_import: bool
    # The edited month was already in a snapshot taken before this edit; the snapshot is unchanged.
    post_snapshot: bool = False


class HistoryPage(OutModel):
    items: list[HistoryItem]
    total_items: int
