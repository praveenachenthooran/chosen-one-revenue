"""Probability-weighted pipeline phasing (PRD section 6, "Weighting rule"; FR-08).

Weighted monthly value = deal amount x stage probability, spread evenly over the deal's duration
starting in the month of its expected start date. Pure functions on Decimals: no database, no
floating point. The cent rounding below guarantees that a deal's monthly values add up to exactly
its weighted amount, so totals never drift from the deal that produced them.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MAX_DURATION_MONTHS = 120


@dataclass(frozen=True)
class Phasing:
    """How one deal's weighted amount falls across the forecast months."""

    weighted_amount: Decimal  # amount x probability, to the cent
    monthly: dict[date, Decimal] = field(default_factory=lambda: {})  # months inside the window
    outside_window: Decimal = Decimal("0.00")  # part before the window opens or after it ends
    phaseable: bool = True
    reason: str | None = None  # why it could not be phased


def weighted_amount(amount: Decimal, probability: Decimal) -> Decimal:
    return (amount * probability).quantize(CENT, rounding=ROUND_HALF_UP)


def month_start(value: date) -> date:
    return value.replace(day=1)


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def phase(
    amount: Decimal | None,
    probability: Decimal,
    expected_start: date | None,
    duration_months: int | None,
    window: list[date],
) -> Phasing:
    """Spread `amount x probability` evenly over `duration_months` from the start month.

    `window` lists the months that are shown (first-of-month dates). Months of the deal that
    fall outside it are not shown but are reported in `outside_window`, so nothing disappears.
    A deal without an amount, start date or duration cannot be phased and is reported, never
    guessed.
    """
    if amount is None:
        return Phasing(Decimal("0.00"), phaseable=False, reason="missing_amount")
    total = weighted_amount(amount, probability)
    if expected_start is None or duration_months is None:
        return Phasing(total, phaseable=False, reason="missing_start_or_duration")
    if not 1 <= duration_months <= MAX_DURATION_MONTHS:
        return Phasing(total, phaseable=False, reason="invalid_duration")

    # Whole cents, distributed so the parts add up exactly: every month gets the rounded-down
    # share and the leftover cents go one each to the earliest months.
    total_cents = int(total / CENT)
    share = (Decimal(total_cents) / duration_months).quantize(Decimal("1"), rounding=ROUND_DOWN)
    leftover = total_cents - int(share) * duration_months

    start = month_start(expected_start)
    shown = set(window)
    monthly: dict[date, Decimal] = {}
    outside = Decimal("0.00")
    for offset in range(duration_months):
        cents = int(share) + (1 if offset < leftover else 0)
        value = Decimal(cents) * CENT
        month = add_months(start, offset)
        if month in shown:
            monthly[month] = value
        else:
            outside += value
    return Phasing(total, monthly, outside)
