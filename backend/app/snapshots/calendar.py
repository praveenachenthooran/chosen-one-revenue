"""Business-day rules for the monthly snapshot.

A business day is Monday to Friday. There is no holiday calendar (the PRD does not supply one and
the draft-confirmation deadline in Milestone 3 uses the same rule), so a public holiday on the
first weekday of a month is still treated as a business day. Dates are UTC.
"""

from datetime import date, timedelta


def month_start(day: date) -> date:
    return day.replace(day=1)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5


def first_business_day(year: int, month: int) -> date:
    day = date(year, month, 1)
    while not is_business_day(day):
        day += timedelta(days=1)
    return day
