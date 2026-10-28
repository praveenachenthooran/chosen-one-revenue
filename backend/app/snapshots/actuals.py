"""Monthly actual revenue: reading a CSV or XLSX file, validating it and reconciling it.

File shape (one row per customer or project and month; column names are matched ignoring case,
spaces and punctuation):

    Month               required   2026-09, 2026-09-30, "Sep 2026" or an Excel date (any day)
    Amount              required   a plain number; $ and commas allowed, (1,200) means -1200
    Project ID          optional   the Chosen One Row ID, e.g. R1001
    Customer            optional   customer name
    Project             optional   project name (with Customer, identifies the project)

Every row needs a Project ID, or a Customer (with a Project to narrow it to one project; without
one the amount is a customer-level actual). An upload is all or nothing: if any row is invalid
nothing is stored and every problem is reported, so the file can be fixed and sent again.
Re-uploading a month replaces earlier values for the same key; the earlier values are kept,
marked superseded, and an identical re-upload changes nothing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    ActualsUpload,
    Customer,
    MonthlyActual,
    Project,
    ProjectMonthlyRevenue,
    User,
)
from app.pipeline.drafts import DRAFT_STATUS

CENT = Decimal("0.01")
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_ERRORS_SHOWN = 200
MAX_AMOUNT = Decimal("999999999999.99")
EARLIEST_MONTH = date(2000, 1, 1)

ALIASES: dict[str, tuple[str, ...]] = {
    "month": ("month", "period", "revenuemonth", "actualmonth", "fiscalmonth"),
    "amount": ("amount", "actual", "actuals", "actualrevenue", "revenue", "actualamount"),
    "project_id": ("projectid", "rowid", "projectrowid", "sourcerowid"),
    "customer": ("customer", "client", "customername", "account"),
    "project": ("project", "projectname", "engagement"),
}
MONTH_NAMES = {
    name: index
    for index, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}  # fmt: skip


class ActualsFileError(Exception):
    """The file as a whole cannot be used (as opposed to a problem on one row)."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class RowIssue:
    row: int
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "code": self.code, "message": self.message, "field": self.field}


@dataclass
class _Row:
    number: int
    month: date | None = None
    amount: Decimal | None = None
    project_id: str | None = None
    customer: str | None = None
    project: str | None = None
    # resolved
    customer_id: int | None = None
    project_pk: int | None = None
    issues: list[RowIssue] = field(default_factory=lambda: [])

    @property
    def key(self) -> tuple[int | None, int | None, date | None]:
        return (self.project_pk, self.customer_id if self.project_pk is None else None, self.month)


@dataclass
class UploadResult:
    status: str  # completed | rejected
    dry_run: bool
    upload: ActualsUpload | None
    report: dict[str, Any]


def normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


# ---- reading the file ----------------------------------------------------------------------


def _read_csv(content: bytes) -> list[list[Any]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes nearly anything, but be explicit
        raise ActualsFileError("unreadable_file", "The CSV could not be decoded as text.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(content: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for a bad file
        raise ActualsFileError(
            "unreadable_file", "The file is not a valid .xlsx workbook."
        ) from exc
    try:
        names = workbook.sheetnames
        sheet = workbook["Actuals"] if "Actuals" in names else workbook[names[0]]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def read_table(filename: str, content: bytes) -> tuple[list[str], list[tuple[int, list[Any]]]]:
    """The header names and the data rows with their 1-based line numbers in the file."""
    if len(content) > MAX_BYTES:
        raise ActualsFileError(
            "file_too_large", f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.", 413
        )
    if not content.strip():
        raise ActualsFileError("empty_file", "The file is empty.")
    lower = filename.lower()
    if lower.endswith(".csv"):
        raw = _read_csv(content)
    elif lower.endswith(".xlsx"):
        raw = _read_xlsx(content)
    else:
        raise ActualsFileError(
            "unsupported_file_type",
            "Upload a .csv or .xlsx file (older .xls is not supported).",
            415,
        )
    numbered = [(i, row) for i, row in enumerate(raw, start=1)]
    numbered = [(i, r) for i, r in numbered if any(str(c).strip() for c in r if c is not None)]
    if not numbered:
        raise ActualsFileError("empty_file", "The file has no rows.")
    header_row = numbered[0][1]
    headers = [str(c).strip() if c is not None else "" for c in header_row]
    rows = numbered[1:]
    if not rows:
        raise ActualsFileError("no_data_rows", "The file has a header row but no data rows.")
    if len(rows) > MAX_ROWS:
        raise ActualsFileError(
            "too_many_rows", f"The file has more than {MAX_ROWS:,} rows; split it by month.", 413
        )
    return headers, rows


def map_columns(headers: list[str]) -> tuple[dict[str, int], list[str]]:
    """Which column holds which field, and the header names that are ignored."""
    normalised = [normalise_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for fieldname, names in ALIASES.items():
        matches = [i for i, h in enumerate(normalised) if h in names]
        if len(matches) > 1:
            raise ActualsFileError(
                "ambiguous_columns",
                f"More than one column looks like '{fieldname.replace('_', ' ')}': "
                + ", ".join(headers[i] for i in matches)
                + ". Keep one.",
            )
        if matches:
            mapping[fieldname] = matches[0]
    missing = [f for f in ("month", "amount") if f not in mapping]
    if "project_id" not in mapping and "customer" not in mapping:
        missing.append("project_id or customer")
    if missing:
        raise ActualsFileError(
            "missing_columns",
            "The file needs these columns: "
            + ", ".join(m.replace("_", " ") for m in missing)
            + ". Found: "
            + (", ".join(h for h in headers if h) or "none")
            + ".",
        )
    used = set(mapping.values())
    ignored = [h for i, h in enumerate(headers) if i not in used and h]
    return mapping, ignored


# ---- parsing cells -------------------------------------------------------------------------


def parse_month(value: object) -> date | None:
    """The first day of the month a cell refers to, or None if it cannot be read."""
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[ T].*)?", text)
    if iso:
        return _make_month(int(iso.group(1)), int(iso.group(2)))
    slash = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if slash:
        return _make_month(int(slash.group(2)), int(slash.group(1)))
    named = re.fullmatch(r"([A-Za-z]+)[ \-/]+(\d{4}|\d{2})", text)
    if named and named.group(1).lower() in MONTH_NAMES:
        year = int(named.group(2))
        year += 2000 if year < 100 else 0
        return _make_month(year, MONTH_NAMES[named.group(1).lower()])
    return None


def _make_month(year: int, month: int) -> date | None:
    return date(year, month, 1) if 1 <= month <= 12 and 1900 <= year <= 2999 else None


def parse_amount(value: object) -> tuple[Decimal | None, str | None]:
    """The amount in whole cents (half-up), and a note when it had to be rounded."""
    if isinstance(value, bool) or value is None:
        return None, None
    text = str(value) if isinstance(value, int | float | Decimal) else str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[\s$,()]", "", text)
    if not cleaned or not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", cleaned):
        return None, None
    try:
        number = Decimal(cleaned)
    except InvalidOperation:  # pragma: no cover - the pattern already guarantees a number
        return None, None
    if not number.is_finite():  # pragma: no cover
        return None, None
    if negative:
        number = -abs(number)
    rounded = number.quantize(CENT, rounding=ROUND_HALF_UP)
    note = f"rounded from {number.normalize():f}" if rounded != number else None
    return rounded, note


def _cell(row: list[Any], index: int | None) -> Any:
    return row[index] if index is not None and index < len(row) else None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


# ---- the upload ------------------------------------------------------------------------------


async def forecast_now(session: AsyncSession, months: list[date]) -> dict[date, Decimal]:
    """The committed forecast now (drafts excluded) for each month, for the reconciliation."""
    rows = await session.execute(
        select(
            ProjectMonthlyRevenue.month, func.coalesce(func.sum(ProjectMonthlyRevenue.amount), 0)
        )
        .join(Project, Project.id == ProjectMonthlyRevenue.project_id)
        .where(Project.status != DRAFT_STATUS, ProjectMonthlyRevenue.month.in_(months))
        .group_by(ProjectMonthlyRevenue.month)
    )
    return {month: Decimal(total) for month, total in rows}


async def process_upload(
    session: AsyncSession,
    *,
    filename: str,
    content: bytes,
    actor: User | None,
    now: datetime,
    dry_run: bool = False,
) -> UploadResult:
    """Validate and (unless `dry_run`) store an actuals file; the caller commits.

    File-level problems raise `ActualsFileError`. Row problems produce a `rejected` result with
    every issue listed (and, unless `dry_run`, a rejected upload record for the audit trail).
    """
    headers, raw_rows = read_table(filename, content)
    mapping, ignored = map_columns(headers)
    current_month = now.astimezone(UTC).date().replace(day=1)

    rows: list[_Row] = []
    for number, cells in raw_rows:
        row = _Row(
            number=number,
            project_id=_text(_cell(cells, mapping.get("project_id"))),
            customer=_text(_cell(cells, mapping.get("customer"))),
            project=_text(_cell(cells, mapping.get("project"))),
        )
        rows.append(row)
        month_cell = _cell(cells, mapping["month"])
        row.month = parse_month(month_cell)
        if row.month is None:
            shown = "blank" if _text(month_cell) is None else f"'{_text(month_cell)}'"
            row.issues.append(
                RowIssue(
                    number,
                    "invalid_month",
                    f"Month {shown} is not a month (use 2026-09 or Sep 2026).",
                    "month",
                )
            )
        elif row.month < EARLIEST_MONTH or row.month > current_month:
            row.issues.append(
                RowIssue(
                    number, "month_not_allowed",
                    f"{row.month:%b %Y} is in the future: actuals can only be loaded for months "
                    "that have started.",
                    "month",
                )
            )  # fmt: skip
        amount_cell = _cell(cells, mapping["amount"])
        row.amount, rounding_note = parse_amount(amount_cell)
        if row.amount is None:
            shown = "blank" if _text(amount_cell) is None else f"'{_text(amount_cell)}'"
            row.issues.append(
                RowIssue(number, "invalid_amount", f"Amount {shown} is not a number.", "amount")
            )
        elif abs(row.amount) > MAX_AMOUNT:
            row.issues.append(
                RowIssue(number, "amount_too_large", "Amount is too large.", "amount")
            )
        if not row.project_id and not row.customer:
            row.issues.append(
                RowIssue(number, "no_identifier", "Give a Project ID, or a Customer.", "customer")
            )
        if rounding_note:
            row.issues.append(
                RowIssue(number, "rounded", f"Amount {rounding_note} to cents.", "amount")
            )

    await _resolve(session, rows)
    stored = await _stored_active(session, {r.month for r in rows if r.month})
    _check_duplicates_and_overlap(rows, stored)

    errors = [i for r in rows for i in r.issues if i.code != "rounded"]
    warnings = [i for r in rows for i in r.issues if i.code == "rounded"]
    bad_rows = {i.row for i in errors}
    good = [r for r in rows if r.number not in bad_rows]
    months = sorted({r.month for r in rows if r.month})
    forecast = await forecast_now(session, months)

    report: dict[str, Any] = {
        "filename": filename,
        "rows_total": len(rows),
        "rows_rejected": len(bad_rows),
        "ignored_columns": ignored,
        "errors": [e.as_dict() for e in errors[:MAX_ERRORS_SHOWN]],
        "errors_total": len(errors),
        "warnings": [w.as_dict() for w in warnings[:MAX_ERRORS_SHOWN]],
    }
    rejected = bool(errors)
    now_utc = now.astimezone(UTC)
    to_store: list[_Row] = []
    new = unchanged = replaced = 0
    if not rejected:
        for row in good:
            existing = stored.get(row.key)
            if existing is not None and existing.amount == row.amount:
                unchanged += 1
                continue
            if existing is not None:
                replaced += 1
            else:
                new += 1
            to_store.append(row)
        report.update(rows_new=new, rows_replaced=replaced, rows_unchanged=unchanged)

    by_month: list[dict[str, Any]] = []
    for month in months:
        month_rows = [r for r in rows if r.month == month and r.amount is not None]
        total = sum((r.amount for r in month_rows if r.amount is not None), Decimal("0.00"))
        by_month.append(
            {
                "month": month.isoformat(),
                "rows": len(month_rows),
                "actual_total": str(total),
                "committed_forecast_now": str(forecast.get(month, Decimal("0.00"))),
            }
        )
    total_amount = sum((r.amount for r in good if r.amount is not None), Decimal("0.00"))
    report.update(
        total_amount=str(total_amount),
        project_level_rows=sum(1 for r in good if r.project_pk is not None),
        customer_level_rows=sum(1 for r in good if r.project_pk is None),
        customers=len({r.customer_id for r in good if r.customer_id}),
        months=by_month,
    )
    # The upload row is immutable once written, so everything on it is final before it is added.
    upload = ActualsUpload(
        filename=filename[:300],
        sha256=hashlib.sha256(content).hexdigest(),
        status="rejected" if rejected else "completed",
        uploaded_by_id=actor.id if actor else None,
        uploaded_by_label=actor.name if actor else "command line",
        uploaded_at=now_utc,
        rows_total=len(rows),
        rows_accepted=0 if rejected else len(rows),
        rows_rejected=len(bad_rows),
        rows_unchanged=unchanged,
        rows_replaced=replaced,
        total_amount=total_amount,
        months=[m.isoformat() for m in months],
        report=report,
    )
    if dry_run:
        return UploadResult("rejected" if rejected else "completed", True, None, report)
    if rejected:
        session.add(upload)  # kept for the audit trail: who tried to load what, and why it failed
        await session.flush()
    else:
        await _store(session, upload, to_store, stored, now_utc)
    return UploadResult("rejected" if rejected else "completed", False, upload, report)


async def _resolve(session: AsyncSession, rows: list[_Row]) -> None:
    projects = list(await session.scalars(select(Project).options(selectinload(Project.customer))))
    customers = {c.name.lower(): c for c in await session.scalars(select(Customer))}
    by_row_id = {p.source_row_id.lower(): p for p in projects}
    by_name: dict[tuple[str, str], list[Project]] = {}
    for p in projects:
        by_name.setdefault((p.customer.name.lower(), p.name.lower()), []).append(p)

    for row in rows:
        customer = customers.get(row.customer.lower()) if row.customer else None
        if row.project_id:
            project = by_row_id.get(row.project_id.lower())
            if project is None:
                row.issues.append(
                    RowIssue(
                        row.number,
                        "unknown_project",
                        f"No project has the ID '{row.project_id}'.",
                        "project_id",
                    )
                )
                continue
            if row.customer and (customer is None or customer.id != project.customer_id):
                row.issues.append(
                    RowIssue(
                        row.number, "customer_mismatch",
                        f"Project {project.source_row_id} belongs to {project.customer.name}, "
                        f"not '{row.customer}'.",
                        "customer",
                    )
                )  # fmt: skip
                continue
            if row.project and row.project.lower() != project.name.lower():
                row.issues.append(
                    RowIssue(
                        row.number, "project_mismatch",
                        f"Project {project.source_row_id} is '{project.name}', "
                        f"not '{row.project}'.",
                        "project",
                    )
                )  # fmt: skip
                continue
            row.project_pk, row.customer_id = project.id, project.customer_id
        elif row.customer:
            if customer is None:
                row.issues.append(
                    RowIssue(
                        row.number,
                        "unknown_customer",
                        f"No customer is named '{row.customer}'.",
                        "customer",
                    )
                )
                continue
            row.customer_id = customer.id
            if row.project:
                found = by_name.get((customer.name.lower(), row.project.lower()), [])
                if not found:
                    row.issues.append(
                        RowIssue(
                            row.number, "unknown_project",
                            f"{customer.name} has no project named '{row.project}'.", "project",
                        )
                    )  # fmt: skip
                    continue
                row.project_pk = found[0].id


async def _stored_active(
    session: AsyncSession, months: set[date]
) -> dict[tuple[int | None, int | None, date | None], MonthlyActual]:
    if not months:
        return {}
    rows = await session.scalars(
        select(MonthlyActual).where(
            MonthlyActual.superseded_at.is_(None), MonthlyActual.month.in_(months)
        )
    )
    return {
        (a.project_id, a.customer_id if a.project_id is None else None, a.month): a for a in rows
    }


def _check_duplicates_and_overlap(
    rows: list[_Row],
    stored: dict[tuple[int | None, int | None, date | None], MonthlyActual],
) -> None:
    """Two rows for one key, or customer-level and project-level actuals for one customer-month,
    would be double counted, so they are rejected rather than summed or guessed at."""
    first: dict[tuple[int | None, int | None, date | None], int] = {}
    levels: dict[tuple[int, date], dict[str, int]] = {}  # (customer, month) -> level -> row number
    for row in rows:
        if (
            row.month is None
            or row.customer_id is None
            or any(i.code != "rounded" for i in row.issues)
        ):
            continue
        if row.key in first:
            row.issues.append(
                RowIssue(
                    row.number, "duplicate_row",
                    f"Duplicates row {first[row.key]} (same customer/project and month).", "month",
                )
            )  # fmt: skip
            continue
        first[row.key] = row.number
        level = "project" if row.project_pk is not None else "customer"
        levels.setdefault((row.customer_id, row.month), {})[level] = row.number

    # Values already on record that this file does not replace still count.
    kept: dict[tuple[int, date], set[str]] = {}
    for key, actual in stored.items():
        if key not in first:
            level = "project" if actual.project_id is not None else "customer"
            kept.setdefault((actual.customer_id, actual.month), set()).add(level)
    by_number = {r.number: r for r in rows}
    for (customer_id, month), in_file in levels.items():
        if len(set(in_file) | kept.get((customer_id, month), set())) > 1:
            number = max(in_file.values())
            by_number[number].issues.append(
                RowIssue(
                    number, "level_overlap",
                    "This customer would have both customer-level and project-level actuals "
                    f"for {month:%b %Y}, which would be counted twice. Use one level.",
                    "customer",
                )
            )  # fmt: skip


async def _store(
    session: AsyncSession,
    upload: ActualsUpload,
    rows: list[_Row],
    stored: dict[tuple[int | None, int | None, date | None], MonthlyActual],
    now: datetime,
) -> None:
    session.add(upload)
    await session.flush()
    superseded = False
    for row in rows:
        old = stored.get(row.key)
        if old is not None:
            old.superseded_at = now
            old.superseded_by_upload_id = upload.id
            superseded = True
    if superseded:
        await session.flush()  # free the unique keys before inserting the replacements
    for row in rows:
        assert row.customer_id is not None and row.month is not None and row.amount is not None
        session.add(
            MonthlyActual(
                upload_id=upload.id, customer_id=row.customer_id, project_id=row.project_pk,
                month=row.month, amount=row.amount,
            )
        )  # fmt: skip
    await session.flush()
