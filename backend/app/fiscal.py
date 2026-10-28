"""Fiscal calendar helpers.

BIG's fiscal year runs April to March (FY27 = Apr 2026 - Mar 2027). This is what the
supplied workbook's README states and what PRD section 5 says. PRD section 9
("Assumptions") says July-June instead; that is a known PRD inconsistency and is
deliberately not used. A later business meeting said September: the start month is now the
`FISCAL_YEAR_START_MONTH` setting (default 4) so the business can switch the reporting year
without a code change. See docs/milestone-1-assumptions.md and docs/biggie-revenue-ui.md.
"""

from datetime import date

from app.config import get_settings

FISCAL_YEAR_START_MONTH = get_settings().fiscal_year_start_month


def fiscal_year(month: date, start_month: int | None = None) -> str:
    """Return the fiscal year label, e.g. FY27 for any date from 2026-04-01 to 2027-03-31."""
    start = start_month or FISCAL_YEAR_START_MONTH
    year = month.year + 1 if month.month >= start else month.year
    return f"FY{year % 100:02d}"


def fiscal_period(month: date, start_month: int | None = None) -> int:
    """Return the 1-based fiscal period (the start month = 1; April by default)."""
    return (month.month - (start_month or FISCAL_YEAR_START_MONTH)) % 12 + 1
