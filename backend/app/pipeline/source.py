"""Where HubSpot deals come from: a small adapter interface and the workbook implementation.

`DealSource` is the seam for a future live integration: anything that can produce a
`SourceSnapshot` can feed the sync. The live HubSpot adapter must stay strictly read-only (GET
requests with a read-only private-app token). Nothing here, or in the sync, writes to HubSpot.

The workbook adapter uses explicit header mappings and never invents a missing field: an
absent start date, duration, amount or owner stays absent and is reported as a finding.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Protocol

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

SHEET_OPEN = "On_Deck"
SHEET_CLOSED = "Recent_Closed"
SHEET_STAGES = "Stage_Probabilities"
SHEET_LINKS = "Deal_ChosenOne_Map"
SHEET_README = "README"

# Source column -> meaning. Headers are matched by name, never by position.
COLUMNS = {
    "deal_id": "Record ID",
    "name": "Deal Name",
    "company": "Associated Company (Primary)",
    "amount": "Amount in company currency",
    "owner": "Deal owner",
    "stage": "Deal Stage",
    "close_date": "Close Date",
    "company_id": "Associated Company IDs (Primary)",
    "practice": "TECH (proposed)",
    "region": "Region (proposed)",
    "start": "Expected Start (proposed)",
    "duration": "Duration months (proposed)",
}
REQUIRED = ("deal_id", "name", "stage")
WON, LOST = "Closed Won", "Closed Lost"
AMOUNT_MAX_DECIMALS = 4


class SourceError(Exception):
    """The source could not be read at all. The sync keeps its last good data."""


@dataclass(frozen=True)
class SourceFinding:
    severity: str  # error | warning | info
    code: str
    message: str
    deal_id: str | None = None
    details: dict[str, object] | None = None


@dataclass
class SourceStage:
    name: str
    probability: Decimal
    kind: str  # open | won | lost
    in_forecast: bool
    position: int
    note: str | None = None


@dataclass
class SourceDeal:
    deal_id: str
    name: str
    stage: str
    company_name: str | None  # None when HubSpot only supplied a numeric ID in the name field
    company_raw: str | None
    company_id: str | None
    owner_name: str | None
    amount: Decimal | None
    close_date: datetime | None
    expected_start: date | None
    duration_months: int | None
    practice: str | None
    region: str | None
    raw: dict[str, object]
    sheet: str
    row_number: int


@dataclass(frozen=True)
class SourceLink:
    deal_id: str
    project_row_id: str  # the Chosen One workbook Row ID, e.g. R1007


@dataclass
class SourceSnapshot:
    source: str
    source_ref: str | None
    as_of: date | None
    deals: list[SourceDeal]
    links: list[SourceLink]
    stages: list[SourceStage]
    # True when `deals` is the complete set of deals at or beyond the forecast stage, so a deal
    # missing from it has left the forecast. Partial feeds must set this to False.
    complete: bool
    findings: list[SourceFinding] = field(default_factory=lambda: [])


class DealSource(Protocol):
    name: str

    def fetch(self) -> SourceSnapshot:
        """Return the current deals. Must be read-only and raise SourceError on failure."""
        ...


class HubSpotApiSource:
    """Placeholder for the live, read-only HubSpot integration (a later milestone).

    A real implementation would call HubSpot's CRM *read* endpoints with the private-app token
    (HUBSPOT_ACCESS_TOKEN: deals, associated companies, owners, pipeline stages with their
    probabilities), map them onto `SourceSnapshot`, and never issue anything but GET requests.
    """

    name = "api"

    def __init__(self, access_token: str | None) -> None:
        self._token = access_token

    def fetch(self) -> SourceSnapshot:
        if not self._token:
            raise SourceError(
                "HUBSPOT_ACCESS_TOKEN is not set; the live HubSpot adapter is unavailable."
            )
        raise SourceError(
            "The live HubSpot adapter is not implemented yet; use HUBSPOT_SOURCE=workbook."
        )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        try:
            return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
        except InvalidOperation:
            return None
    return None


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _parse_close_date(value: object) -> datetime | None:
    """HubSpot exports close dates as 'YYYY-MM-DD HH:MM'. The export states no time zone;
    UTC is assumed (HubSpot stores and exports timestamps in UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = _clean(value)
    if text is None:
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _is_numeric_id(value: str | None) -> bool:
    return value is not None and value.isdigit() and len(value) >= 6


class WorkbookDealSource:
    """Reads the supplied HubSpot export workbook (On_Deck + Recent_Closed tabs)."""

    name = "workbook"

    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch(self) -> SourceSnapshot:
        try:
            raw = self.path.read_bytes()
            workbook = openpyxl.load_workbook(BytesIO(raw), data_only=True)
        except (OSError, Exception) as exc:  # unreadable or not an xlsx
            raise SourceError(f"Cannot read the HubSpot workbook {self.path.name}: {exc}") from exc

        for sheet in (SHEET_OPEN, SHEET_STAGES):
            if sheet not in workbook.sheetnames:
                raise SourceError(f"The HubSpot workbook has no '{sheet}' sheet.")

        findings: list[SourceFinding] = []
        stages = self._stages(workbook[SHEET_STAGES])
        deals: list[SourceDeal] = []
        seen: dict[str, str] = {}
        for sheet_name in (SHEET_OPEN, SHEET_CLOSED):
            if sheet_name in workbook.sheetnames:
                deals += self._deals(workbook[sheet_name], sheet_name, seen, findings)
        links = self._links(workbook[SHEET_LINKS]) if SHEET_LINKS in workbook.sheetnames else []
        return SourceSnapshot(
            source=self.name,
            source_ref=self.path.name,
            as_of=self._as_of(workbook),
            deals=deals,
            links=links,
            stages=stages,
            complete=True,
            findings=findings,
        )

    @staticmethod
    def _as_of(workbook: openpyxl.Workbook) -> date | None:
        if SHEET_README not in workbook.sheetnames:
            return None
        for row in workbook[SHEET_README].iter_rows(values_only=True):
            if row and _clean(row[0]) == "As of" and len(row) > 1 and row[1]:
                match = re.search(r"\d{4}-\d{2}-\d{2}", str(row[1]))
                if match:
                    return date.fromisoformat(match.group())
        return None

    @staticmethod
    def _headers(sheet: Worksheet) -> dict[str, int]:
        return {text: c.column for c in sheet[1] if (text := _clean(c.value)) and c.column}

    def _stages(self, sheet: Worksheet) -> list[SourceStage]:
        headers = self._headers(sheet)
        for needed in ("Deal Stage", "Probability", "In forecast?"):
            if needed not in headers:
                raise SourceError(f"Stage_Probabilities is missing the '{needed}' column.")
        stages: list[SourceStage] = []
        for index, row in enumerate(range(2, sheet.max_row + 1)):
            name = _clean(sheet.cell(row, headers["Deal Stage"]).value)
            if name is None:
                continue
            probability = _decimal(sheet.cell(row, headers["Probability"]).value)
            if probability is None or not Decimal(0) <= probability <= Decimal(1):
                raise SourceError(f"Stage '{name}' has a probability outside 0-1: {probability}")
            flag = (_clean(sheet.cell(row, headers["In forecast?"]).value) or "").lower()
            kind = "won" if name == WON else "lost" if name == LOST else "open"
            note = _clean(sheet.cell(row, headers["Note"]).value) if "Note" in headers else None
            stages.append(
                SourceStage(name, probability, kind, flag in ("yes", "committed"), index, note)
            )
        return stages

    def _links(self, sheet: Worksheet) -> list[SourceLink]:
        headers = self._headers(sheet)
        if "Record ID" not in headers or "Chosen One Row ID" not in headers:
            return []
        links: list[SourceLink] = []
        for row in range(2, sheet.max_row + 1):
            deal_id = _clean(sheet.cell(row, headers["Record ID"]).value)
            row_id = _clean(sheet.cell(row, headers["Chosen One Row ID"]).value)
            if deal_id and row_id:
                links.append(SourceLink(deal_id, row_id))
        return links

    def _deals(
        self,
        sheet: Worksheet,
        sheet_name: str,
        seen: dict[str, str],
        findings: list[SourceFinding],
    ) -> list[SourceDeal]:
        headers = self._headers(sheet)
        missing = [COLUMNS[k] for k in REQUIRED if COLUMNS[k] not in headers]
        if missing:
            raise SourceError(f"{sheet_name} is missing required column(s): {', '.join(missing)}")

        def cell(row: int, key: str) -> object:
            column = headers.get(COLUMNS[key])
            return sheet.cell(row, column).value if column else None

        deals: list[SourceDeal] = []
        for row in range(2, sheet.max_row + 1):
            if all(sheet.cell(row, c).value is None for c in range(1, sheet.max_column + 1)):
                continue
            deal_id = _clean(cell(row, "deal_id"))
            where = f"{sheet_name} row {row}"
            if deal_id is None:
                findings.append(
                    SourceFinding("error", "missing_deal_id", f"{where}: no Record ID; skipped.")
                )
                continue
            if deal_id in seen:
                findings.append(
                    SourceFinding(
                        "error",
                        "duplicate_deal_id",
                        f"{where}: Record ID {deal_id} already appeared on "
                        f"{seen[deal_id]}; skipped.",
                        deal_id,
                    )
                )
                continue
            seen[deal_id] = where
            name, stage = _clean(cell(row, "name")), _clean(cell(row, "stage"))
            if name is None or stage is None:
                findings.append(
                    SourceFinding(
                        "error",
                        "missing_required_field",
                        f"{where}: needs a Deal Name and Deal Stage; skipped.",
                        deal_id,
                    )
                )
                continue

            def note(severity: str, code: str, message: str, _id: str = deal_id) -> None:
                findings.append(SourceFinding(severity, code, message, _id))

            company_raw = _clean(cell(row, "company"))
            company_id = _clean(cell(row, "company_id"))
            # HubSpot sometimes exports the company ID in the name column (PRD H8): resolve by
            # ID, never by that text, and say so.
            if company_raw is not None and (
                _is_numeric_id(company_raw) or company_raw == company_id
            ):
                note(
                    "info",
                    "company_name_is_id",
                    "The company field holds an ID, so the company name is unknown.",
                )
                company_name = None
            else:
                company_name = company_raw

            amount_raw = cell(row, "amount")
            amount = _decimal(amount_raw)
            if amount_raw is not None and amount is None:
                note(
                    "error",
                    "invalid_amount",
                    f"Amount {amount_raw!r} is not a number; deal cannot be weighted.",
                )
            elif amount is not None and amount < 0:
                note("error", "invalid_amount", "Amount is negative; deal cannot be weighted.")
                amount = None
            elif amount is not None and _decimal_places(amount) > AMOUNT_MAX_DECIMALS:
                note(
                    "error",
                    "invalid_amount",
                    f"Amount has more than {AMOUNT_MAX_DECIMALS} decimal places.",
                )
                amount = None
            elif amount is None:
                note("warning", "missing_amount", "No amount; deal cannot be weighted.")

            start_raw = cell(row, "start")
            start: date | None = None
            if isinstance(start_raw, datetime):
                start = start_raw.date()
            elif isinstance(start_raw, date):
                start = start_raw
            elif start_raw is not None:
                note("warning", "invalid_start", f"Expected start {start_raw!r} is not a date.")

            duration_raw = cell(row, "duration")
            duration: int | None = None
            parsed = _decimal(duration_raw)
            if duration_raw is not None and (
                parsed is None or parsed != parsed.to_integral_value() or parsed < 1
            ):
                note(
                    "warning",
                    "invalid_duration",
                    f"Duration {duration_raw!r} is not a whole number of months.",
                )
            elif parsed is not None:
                duration = int(parsed)

            close_raw = cell(row, "close_date")
            close = _parse_close_date(close_raw)
            if close_raw is not None and close is None:
                note(
                    "warning", "invalid_close_date", f"Close date {close_raw!r} is not understood."
                )

            if start is None or duration is None:
                note(
                    "warning",
                    "missing_start_or_duration",
                    "No expected start or duration, so the deal cannot be phased by month.",
                )

            deals.append(
                SourceDeal(
                    deal_id=deal_id,
                    name=name,
                    stage=stage,
                    company_name=company_name,
                    company_raw=company_raw,
                    company_id=company_id,
                    owner_name=_clean(cell(row, "owner")),
                    amount=amount,
                    close_date=close,
                    expected_start=start,
                    duration_months=duration,
                    practice=_clean(cell(row, "practice")),
                    region=_clean(cell(row, "region")),
                    raw={COLUMNS[k]: _jsonable(cell(row, k)) for k in COLUMNS},
                    sheet=sheet_name,
                    row_number=row,
                )
            )
        return deals


def _jsonable(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "reference/03-demo-data/HubSpot_OnDeck_Dummy_2026-10-01.xlsx"
)


def build_source(source: str, workbook_path: str | None, access_token: str | None) -> DealSource:
    """The configured deal source (HUBSPOT_SOURCE): the workbook export, or the future live API."""
    if source == "api":
        return HubSpotApiSource(access_token)
    return WorkbookDealSource(Path(workbook_path) if workbook_path else DEFAULT_WORKBOOK)
