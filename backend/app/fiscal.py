"""Fiscal calendar helpers.

BIG's fiscal year runs April to March (FY27 = Apr 2026 - Mar 2027). This is what the
supplied workbook's README states and what PRD section 5 says. PRD section 9
("Assumptions") says July-June instead; that is a known PRD inconsistency and is
deliberately not used. See docs/milestone-1-assumptions.md.
"""

from datetime import date

FISCAL_YEAR_START_MONTH = 4


def fiscal_year(month: date) -> str:
    """Return the fiscal year label, e.g. FY27 for any date from 2026-04-01 to 2027-03-31."""
    year = month.year + 1 if month.month >= FISCAL_YEAR_START_MONTH else month.year
    return f"FY{year % 100:02d}"


def fiscal_period(month: date) -> int:
    """Return the 1-based fiscal period (April = 1, March = 12)."""
    return (month.month - FISCAL_YEAR_START_MONTH) % 12 + 1
