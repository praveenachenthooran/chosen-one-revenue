"""Forecast accuracy against the M-1, M-2 and M-3 snapshots."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.snapshots.accuracy import (
    TOLERANCE_PERCENT,
    LagResult,
    accuracy_report,
    is_flagged,
    percent,
)
from app.snapshots.actuals import process_upload
from app.snapshots.service import SCHEDULED
from tests.pipeline_helpers import sdeal
from tests.reporting_helpers import csv_file, set_amount, take
from tests.test_pipeline_api import Env, env  # noqa: F401  # pyright: ignore[reportUnusedImport]

D = Decimal
OCT, NOV, DEC = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)
JUL_1 = datetime(2026, 7, 1, 9, tzinfo=UTC)
AUG_3 = datetime(2026, 8, 3, 9, tzinfo=UTC)  # Aug 1 is a Saturday
SEP_1 = datetime(2026, 9, 1, 9, tzinfo=UTC)
OCT_1 = datetime(2026, 10, 1, 9, tzinfo=UTC)
LATE = datetime(2026, 12, 15, 9, tzinfo=UTC)


# ---- the arithmetic -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("forecast", "actual", "pct", "flagged"),
    [
        ("100", "100", "0.00", False),
        ("105", "100", "5.00", False),  # exactly on the limit is within it
        ("95", "100", "-5.00", False),
        ("105.01", "100", "5.01", True),
        ("94.99", "100", "-5.01", True),
        ("3100", "3000", "3.33", False),
        ("3200", "3000", "6.67", True),
        ("9000", "9500", "-5.26", True),
        ("0", "100", "-100.00", True),
        ("1", "1000000", "-100.00", True),
        ("-100", "-100", "0.00", False),  # credits behave the same way
        ("-105", "-100", "-5.00", False),
    ],
)
def test_percentage_variance_and_the_five_percent_flag(
    forecast: str, actual: str, pct: str, flagged: bool
) -> None:
    f, a = D(forecast), D(actual)
    assert percent(f - a, a) == D(pct)
    assert is_flagged(f, a, TOLERANCE_PERCENT) is flagged


def test_a_zero_actual_has_no_percentage_and_is_flagged_only_if_something_was_forecast() -> None:
    assert percent(D("500"), D("0")) is None
    assert is_flagged(D("500"), D("0"), TOLERANCE_PERCENT) is True
    assert is_flagged(D("0"), D("0"), TOLERANCE_PERCENT) is False


def test_the_tolerance_is_the_prd_five_percent() -> None:
    assert D("5") == TOLERANCE_PERCENT


# ---- the report ---------------------------------------------------------------------------


async def seed_history(env: Env) -> None:
    """Oct 2026 as forecast three, two and one months ahead: 3000, then 3100, then 3200."""
    await take(env.session, JUL_1, kind=SCHEDULED)
    await set_amount(env.session, "R1", OCT, "1100.00")
    await take(env.session, AUG_3, kind=SCHEDULED)
    await set_amount(env.session, "R1", OCT, "1200.00")
    await take(env.session, SEP_1, kind=SCHEDULED)


async def load(env: Env, *rows: str, now: datetime = LATE) -> None:
    result = await process_upload(
        env.session, filename="a.csv", content=csv_file(*rows), actor=None, now=now
    )
    assert result.status == "completed", result.report["errors"]
    await env.session.commit()


def lag(report_month: object, n: int) -> LagResult:
    return next(r for r in report_month.lags if r.lag == n)  # type: ignore[attr-defined]


async def test_variance_by_lag_and_flag_for_one_month(env: Env) -> None:
    await seed_history(env)
    await load(
        env,
        "2026-10,Kestrel Life,Policy Admin,1000",
        "2026-10,Granitepeak Telecom,MuleSoft API Program,1000",
        "2026-10,Initech,Existing Work,1000",
    )
    report = await accuracy_report(env.session)

    assert report.tolerance_percent == D("5") and report.target_lag == 3
    (october,) = report.months
    assert october.month == OCT and october.fiscal_year == "FY27" and october.actual == D("3000.00")
    m3, m2, m1 = lag(october, 3), lag(october, 2), lag(october, 1)
    assert (m3.forecast, m3.variance, m3.variance_percent, m3.flagged) == (
        D("3000.00"),
        D("0.00"),
        D("0.00"),
        False,
    )
    assert (m2.forecast, m2.variance, m2.variance_percent, m2.flagged) == (
        D("3100.00"),
        D("100.00"),
        D("3.33"),
        False,
    )
    assert (m1.forecast, m1.variance, m1.variance_percent, m1.flagged) == (
        D("3200.00"),
        D("200.00"),
        D("6.67"),
        True,
    )
    assert (m3.committed, m3.weighted) == (
        D("3000.00"),
        D("0.00"),
    ) and m3.snapshot_label.startswith("2026-07")  # type: ignore[union-attr]
    assert m3.snapshot_taken_at == JUL_1
    totals = {t.lag: t for t in report.totals}
    assert (totals[3].months_compared, totals[3].months_flagged, totals[3].flagged) == (1, 0, False)
    assert (totals[1].months_compared, totals[1].months_flagged, totals[1].flagged) == (1, 1, True)


async def test_the_forecast_is_committed_plus_weighted(env: Env) -> None:
    await env.load(
        [sdeal("1", start=date(2026, 10, 1), duration=2, amount="20000")]
    )  # 5,000 a month at 50%
    await take(env.session, JUL_1, kind=SCHEDULED)
    await load(env, "2026-10,Kestrel Life,Policy Admin,8000")
    (october,) = (await accuracy_report(env.session)).months
    m3 = lag(october, 3)
    assert (m3.committed, m3.weighted, m3.forecast) == (D("3000.00"), D("5000.00"), D("8000.00"))
    assert (m3.variance_percent, m3.flagged) == (D("0.00"), False)


async def test_totals_cover_only_months_that_have_both_snapshot_and_actual(env: Env) -> None:
    await seed_history(env)
    await take(env.session, OCT_1, kind=SCHEDULED)  # the Oct snapshot is M-1 for November
    await load(
        env,
        "2026-10,Kestrel Life,,3000",
        "2026-11,Kestrel Life,,6500",
    )
    report = await accuracy_report(env.session)
    october, november = report.months

    # November: M-3 is the August snapshot (6000 committed), M-2 the September one (6000).
    n3, n2, n1 = lag(november, 3), lag(november, 2), lag(november, 1)
    assert (n3.forecast, n3.variance_percent, n3.flagged) == (D("6000.00"), D("-7.69"), True)
    assert (n2.forecast, n1.forecast) == (D("6000.00"), D("6000.00"))
    assert lag(october, 3).status == "ok"

    totals = {t.lag: t for t in report.totals}
    assert totals[3].months_compared == 2
    assert (totals[3].forecast, totals[3].actual, totals[3].variance) == (
        D("9000.00"),
        D("9500.00"),
        D("-500.00"),
    )
    assert (totals[3].variance_percent, totals[3].flagged) == (D("-5.26"), True)
    assert (totals[3].months_flagged) == 1


async def test_missing_snapshots_are_shown_as_such_never_estimated(env: Env) -> None:
    await take(env.session, AUG_3, kind=SCHEDULED)  # only the M-2 snapshot for October exists
    await load(env, "2026-10,Kestrel Life,,3000", "2026-09,Kestrel Life,,100")
    report = await accuracy_report(env.session)
    by_month = {m.month: m for m in report.months}

    october = by_month[OCT]
    assert [(r.lag, r.status) for r in october.lags] == [
        (1, "no_snapshot"),
        (2, "ok"),
        (3, "no_snapshot"),
    ]
    assert lag(october, 3).forecast is None and lag(october, 3).variance_percent is None
    assert lag(october, 3).flagged is False
    # September is before the forecast window, so even the August snapshot says nothing about it.
    september = by_month[date(2026, 9, 1)]
    assert {r.status for r in september.lags} == {"no_snapshot", "month_not_in_snapshot"}
    totals = {t.lag: t for t in report.totals}
    assert totals[3].months_compared == 0 and totals[3].variance_percent is None
    assert totals[2].months_compared == 1


async def test_the_scheduled_snapshot_is_the_month_start_snapshot(env: Env) -> None:
    await take(env.session, datetime(2026, 7, 2, 9, tzinfo=UTC))  # a manual one, earlier
    await set_amount(env.session, "R1", OCT, "5000.00")
    await take(env.session, datetime(2026, 7, 3, 9, tzinfo=UTC), kind=SCHEDULED)
    await load(env, "2026-10,Kestrel Life,,1")
    (october,) = (await accuracy_report(env.session)).months
    assert lag(october, 3).forecast == D("7000.00")  # the scheduled one, not the earlier manual


async def test_with_no_actuals_the_report_is_empty(env: Env) -> None:
    await seed_history(env)
    report = await accuracy_report(env.session)
    assert report.months == [] and [t.months_compared for t in report.totals] == [0, 0, 0]


async def test_corrected_actuals_are_what_the_report_uses(env: Env) -> None:
    await seed_history(env)
    await load(env, "2026-10,Kestrel Life,,2000")
    assert lag((await accuracy_report(env.session)).months[0], 3).flagged is True
    await load(env, "2026-10,Kestrel Life,,3000")  # Finance corrects the figure
    month = (await accuracy_report(env.session)).months[0]
    assert month.actual == D("3000.00") and lag(month, 3).flagged is False
