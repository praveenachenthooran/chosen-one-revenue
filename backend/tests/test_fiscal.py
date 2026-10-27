from datetime import date

from app.fiscal import fiscal_period, fiscal_year


def test_fiscal_year_runs_april_to_march() -> None:
    assert fiscal_year(date(2026, 4, 1)) == "FY27"
    assert fiscal_year(date(2027, 3, 1)) == "FY27"
    assert fiscal_year(date(2027, 4, 1)) == "FY28"
    assert fiscal_year(date(2025, 10, 1)) == "FY26"


def test_fiscal_period_april_is_one() -> None:
    assert fiscal_period(date(2026, 4, 1)) == 1
    assert fiscal_period(date(2026, 10, 1)) == 7
    assert fiscal_period(date(2027, 3, 1)) == 12
