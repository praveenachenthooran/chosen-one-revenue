"""Weighted pipeline phasing: pure calculations, no database."""

from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.weighting import add_months, phase, weighted_amount

D = Decimal
WINDOW = [date(2026, 10, 1), *(add_months(date(2026, 10, 1), i) for i in range(1, 12))]


def test_prd_acceptance_example_is_exact() -> None:
    """PRD: a $120,000 deal at 50% starting Nov 1 for 6 months shows $10,000 per month Nov-Apr."""
    result = phase(D("120000"), D("0.5"), date(2026, 11, 1), 6, WINDOW)

    assert result.weighted_amount == D("60000.00")
    assert result.monthly == {
        date(2026, 11, 1): D("10000.00"),
        date(2026, 12, 1): D("10000.00"),
        date(2027, 1, 1): D("10000.00"),
        date(2027, 2, 1): D("10000.00"),
        date(2027, 3, 1): D("10000.00"),
        date(2027, 4, 1): D("10000.00"),
    }
    assert result.outside_window == D("0.00") and result.phaseable


def test_only_the_start_month_matters_not_the_day() -> None:
    mid_month = phase(D("1200"), D("1"), date(2026, 12, 19), 3, WINDOW)
    assert list(mid_month.monthly) == [date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


def test_values_match_the_workbook_preview() -> None:
    """Ravenscar R3 in HubSpot_OnDeck: 598,440.60 x 60% over 9 months from Dec 2026."""
    result = phase(D("598440.6"), D("0.6"), date(2026, 12, 11), 9, WINDOW)
    assert result.weighted_amount == D("359064.36")
    assert set(result.monthly.values()) == {D("39896.04")}
    assert len(result.monthly) == 9


def test_cents_are_distributed_so_the_months_add_up_exactly() -> None:
    result = phase(D("100.00"), D("1"), date(2026, 10, 1), 3, WINDOW)
    assert list(result.monthly.values()) == [D("33.34"), D("33.33"), D("33.33")]
    assert sum(result.monthly.values()) == D("100.00")


@pytest.mark.parametrize(
    ("amount", "probability", "duration"),
    [
        ("812000", "0.6", 12),
        ("308409.0508", "0.4", 6),
        ("0.01", "0.5", 7),
        ("999999999.99", "0.8", 11),
        ("515000", "0.2", 12),
        ("45297.7", "0.8", 5),
        ("1", "1", 12),
    ],
)
def test_months_plus_outside_always_equal_the_weighted_amount(
    amount: str, probability: str, duration: int
) -> None:
    for start in (date(2026, 8, 5), date(2026, 10, 1), date(2027, 6, 30), date(2028, 1, 1)):
        result = phase(D(amount), D(probability), start, duration, WINDOW)
        assert sum(result.monthly.values()) + result.outside_window == result.weighted_amount


def test_months_outside_the_window_are_reported_not_dropped() -> None:
    """Starts in Aug 2026 (before the window) and runs 9 months, so two months are in the past."""
    result = phase(D("900"), D("1"), date(2026, 8, 1), 9, WINDOW)
    assert result.outside_window == D("200.00")
    assert result.monthly[date(2026, 10, 1)] == D("100.00")
    beyond = phase(D("1200"), D("1"), date(2027, 8, 1), 12, WINDOW)  # window ends Sep 2027
    assert len(beyond.monthly) == 2 and beyond.outside_window == D("1000.00")


def test_fractional_four_decimal_amounts_are_weighted_exactly() -> None:
    assert weighted_amount(D("308409.0508"), D("0.4")) == D("123363.62")  # 123363.62032
    assert weighted_amount(D("274850.8566"), D("0.6")) == D("164910.51")  # 164910.51396


def test_results_are_decimals_never_floats() -> None:
    result = phase(D("1000"), D("0.3"), date(2026, 10, 1), 7, WINDOW)
    assert isinstance(result.weighted_amount, Decimal)
    assert all(isinstance(v, Decimal) for v in result.monthly.values())


@pytest.mark.parametrize(
    ("amount", "start", "duration", "reason"),
    [
        (None, date(2026, 11, 1), 6, "missing_amount"),
        (D("1000"), None, 6, "missing_start_or_duration"),
        (D("1000"), date(2026, 11, 1), None, "missing_start_or_duration"),
        (D("1000"), None, None, "missing_start_or_duration"),
        (D("1000"), date(2026, 11, 1), 0, "invalid_duration"),
        (D("1000"), date(2026, 11, 1), -3, "invalid_duration"),
        (D("1000"), date(2026, 11, 1), 121, "invalid_duration"),
    ],
)
def test_deals_that_cannot_be_phased_are_reported_never_guessed(
    amount: Decimal | None, start: date | None, duration: int | None, reason: str
) -> None:
    result = phase(amount, D("0.5"), start, duration, WINDOW)
    assert not result.phaseable and result.reason == reason
    assert result.monthly == {}
    if amount is not None:
        assert result.weighted_amount == (amount * D("0.5")).quantize(D("0.01"))


def test_zero_probability_contributes_nothing() -> None:
    result = phase(D("5000"), D("0"), date(2026, 11, 1), 4, WINDOW)
    assert result.weighted_amount == D("0.00") and sum(result.monthly.values()) == D("0.00")


def test_add_months_wraps_years() -> None:
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert add_months(date(2026, 12, 1), 13) == date(2028, 1, 1)
    assert add_months(date(2026, 1, 1), 0) == date(2026, 1, 1)
