"""Actuals uploads: parsing, validation, reconciliation, replacement and atomicity."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActualsUpload, MonthlyActual
from app.snapshots.actuals import (
    ActualsFileError,
    UploadResult,
    map_columns,
    parse_amount,
    parse_month,
    process_upload,
    read_table,
)
from tests.reporting_helpers import csv_file, project_id, xlsx_file
from tests.test_pipeline_api import Env, env  # noqa: F401  # pyright: ignore[reportUnusedImport]

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
OCT, NOV = date(2026, 10, 1), date(2026, 11, 1)


async def upload(
    env: Env, content: bytes, filename: str = "actuals.csv", *, dry_run: bool = False
) -> UploadResult:
    result = await process_upload(
        env.session, filename=filename, content=content, actor=None, now=NOW, dry_run=dry_run
    )
    await env.session.commit()
    return result


async def active(session: AsyncSession) -> list[MonthlyActual]:
    rows = await session.scalars(
        select(MonthlyActual)
        .where(MonthlyActual.superseded_at.is_(None))
        .order_by(MonthlyActual.id)
    )
    return list(rows)


def codes(result: UploadResult) -> set[str]:
    return {e["code"] for e in result.report["errors"]}


# ---- cell parsing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09", date(2026, 9, 1)),
        ("2026-09-30", date(2026, 9, 1)),
        ("2026-9-1", date(2026, 9, 1)),
        ("Sep 2026", date(2026, 9, 1)),
        ("September 2026", date(2026, 9, 1)),
        ("sep-26", date(2026, 9, 1)),
        ("09/2026", date(2026, 9, 1)),
        (datetime(2026, 9, 30, 0, 0), date(2026, 9, 1)),
        (date(2026, 9, 15), date(2026, 9, 1)),
        ("2026-13", None),
        ("2026-00", None),
        ("Smarch 2026", None),
        ("last month", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_month(value: object, expected: date | None) -> None:
    assert parse_month(value) == expected


@pytest.mark.parametrize(
    ("value", "expected", "rounded"),
    [
        ("1234.5", "1234.50", False),
        ("$1,234.50", "1234.50", False),
        (" 12,500 ", "12500.00", False),
        ("(1,200)", "-1200.00", False),
        ("-50", "-50.00", False),
        (1500, "1500.00", False),
        (1500.25, "1500.25", False),
        (Decimal("0.005"), "0.01", True),
        ("10.005", "10.01", True),
        ("1e3", "1000.00", False),
        ("0", "0.00", False),
    ],
)
def test_parse_amount(value: object, expected: str, rounded: bool) -> None:
    amount, note = parse_amount(value)
    assert amount == Decimal(expected) and bool(note) is rounded


@pytest.mark.parametrize(
    "value", ["abc", "", None, True, "12abc", "1,2,x", "NaN", "inf", "=1+1", "--5"]
)
def test_parse_amount_rejects_non_numbers(value: object) -> None:
    assert parse_amount(value)[0] is None


# ---- the file as a whole ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "code", "status"),
    [
        ("a.csv", b"", "empty_file", 400),
        ("a.csv", b"  \n \n", "empty_file", 400),
        ("a.csv", b"Month,Customer,Amount\n", "no_data_rows", 400),
        ("a.xls", b"x", "unsupported_file_type", 415),
        ("a.pdf", b"x", "unsupported_file_type", 415),
        ("a.xlsx", b"this is not a workbook", "unreadable_file", 400),
        ("a.csv", b"Month,Amount\n2026-10,5\n", "missing_columns", 400),
        ("a.csv", b"Customer,Amount\nAcme,5\n", "missing_columns", 400),
        ("a.csv", b"Month,Customer\n2026-10,Acme\n", "missing_columns", 400),
        ("a.csv", b"Month,Amount,Actual,Customer\n2026-10,1,2,Acme\n", "ambiguous_columns", 400),
    ],
)
def test_unusable_files_are_refused_with_a_clear_code(
    name: str, content: bytes, code: str, status: int
) -> None:
    with pytest.raises(ActualsFileError) as caught:
        headers, _ = read_table(name, content)
        map_columns(headers)
    assert (caught.value.code, caught.value.status) == (code, status)


def test_oversized_files_are_refused() -> None:
    with pytest.raises(ActualsFileError) as caught:
        read_table("a.csv", b"x" * (5 * 1024 * 1024 + 1))
    assert (caught.value.code, caught.value.status) == ("file_too_large", 413)


def test_columns_are_found_by_name_ignoring_case_and_punctuation() -> None:
    mapping, ignored = map_columns(["Revenue Month", "Row ID", "ACTUAL_REVENUE", "Notes", ""])
    assert mapping == {"month": 0, "project_id": 1, "amount": 2} and ignored == ["Notes"]


def test_too_many_rows_is_refused() -> None:
    rows = ["2026-10,Acme,P,1"] * 20_001
    with pytest.raises(ActualsFileError) as caught:
        read_table("a.csv", csv_file(*rows))
    assert caught.value.code == "too_many_rows"


def test_semicolon_and_cp1252_csv_files_are_read() -> None:
    content = "Month;Customer;Project;Amount\n2026-10;Café Société;Projet;1200,5\n".encode("cp1252")
    headers, rows = read_table("a.csv", content)
    assert headers == ["Month", "Customer", "Project", "Amount"]
    assert rows[0][1][1] == "Café Société"


# ---- a good upload ------------------------------------------------------------------------


async def test_actuals_are_keyed_to_projects_and_customers_with_a_reconciliation(env: Env) -> None:
    pid = await project_id(env.session, "R1")
    content = (
        b"Month,Project ID,Customer,Project,Amount\n"
        b'2026-10,R1,,,"1,050.25"\n'
        b"2026-10,,Granitepeak Telecom,MuleSoft API Program,980\n"
        b"Oct 2026,,initech,,1000\n"  # customer-level (no project), case-insensitive
        b"2026-11,R1,Kestrel Life,Policy Admin,2000\n"
    )
    result = await upload(env, content)

    assert result.status == "completed" and result.upload is not None
    report = result.report
    assert (report["rows_total"], report["rows_rejected"], report["rows_new"]) == (4, 0, 4)
    assert (report["project_level_rows"], report["customer_level_rows"]) == (3, 1)
    assert report["customers"] == 3 and report["total_amount"] == "5030.25"
    by_month = {m["month"]: m for m in report["months"]}
    assert by_month["2026-10-01"]["actual_total"] == "3030.25"
    assert by_month["2026-10-01"]["committed_forecast_now"] == "3000.00"  # the reconciliation
    assert by_month["2026-11-01"]["actual_total"] == "2000.00"
    assert by_month["2026-11-01"]["committed_forecast_now"] == "6000.00"

    rows = await active(env.session)
    assert len(rows) == 4
    first = rows[0]
    assert (first.project_id, first.month, first.amount) == (pid, OCT, Decimal("1050.25"))
    assert [r.project_id is None for r in rows] == [False, False, True, False]
    stored = await env.session.get(ActualsUpload, result.upload.id)
    assert stored is not None and stored.status == "completed"
    assert (stored.rows_accepted, stored.total_amount) == (4, Decimal("5030.25"))
    assert stored.uploaded_by_label == "command line" and stored.sha256


async def test_xlsx_upload_with_excel_dates_and_numbers(env: Env) -> None:
    content = xlsx_file(
        [
            [datetime(2026, 10, 31), "Kestrel Life", "Policy Admin", 1000.5],
            [datetime(2026, 10, 31), "Granitepeak Telecom", "MuleSoft API Program", 2000],
        ]
    )
    result = await upload(env, content, "actuals.xlsx")
    assert result.status == "completed" and result.report["total_amount"] == "3000.50"
    assert [r.month for r in await active(env.session)] == [OCT, OCT]


async def test_amounts_with_extra_decimals_are_rounded_and_reported(env: Env) -> None:
    result = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,100.005"))
    assert result.status == "completed"
    assert result.report["warnings"][0]["code"] == "rounded"
    assert (await active(env.session))[0].amount == Decimal("100.01")


async def test_negative_credits_are_accepted(env: Env) -> None:
    result = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,(250)"))
    assert result.status == "completed" and (await active(env.session))[0].amount == Decimal(
        "-250.00"
    )


# ---- bad rows: all or nothing ---------------------------------------------------------------


async def test_every_problem_is_listed_and_nothing_is_stored(env: Env) -> None:
    content = csv_file(
        "2026-10,Kestrel Life,Policy Admin,1000",  # fine
        "2026-10,Nobody Inc,Whatever,5",  # unknown customer
        "2026-10,Kestrel Life,Missing Project,5",  # unknown project
        "not-a-month,Kestrel Life,Policy Admin,5",  # bad month
        "2026-10,Kestrel Life,Policy Admin,twelve",  # bad amount
        "2027-06,Kestrel Life,Policy Admin,5",  # future month
        "2026-10,,,5",  # no identifier
        "2026-10,Kestrel Life,Policy Admin,1000",  # duplicate of row 2
    )
    result = await upload(env, content)

    assert result.status == "rejected"
    assert codes(result) == {
        "unknown_customer", "unknown_project", "invalid_month", "invalid_amount",
        "month_not_allowed", "no_identifier", "duplicate_row",
    }  # fmt: skip
    assert result.report["rows_rejected"] == 7 and result.report["errors_total"] >= 7
    assert {e["row"] for e in result.report["errors"]} == {3, 4, 5, 6, 7, 8, 9}
    assert await active(env.session) == []  # not even the good row
    attempt = await env.session.scalar(select(ActualsUpload))
    assert attempt is not None and attempt.status == "rejected" and attempt.rows_accepted == 0


async def test_row_numbers_in_errors_match_the_file(env: Env) -> None:
    result = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,1", "2026-10,Nobody,,1"))
    assert [(e["row"], e["field"]) for e in result.report["errors"]] == [(3, "customer")]


async def test_project_id_must_agree_with_customer_and_project_names(env: Env) -> None:
    content = (
        b"Month,Project ID,Customer,Project,Amount\n"
        b"2026-10,R1,Initech,,1\n"
        b"2026-10,R2,Granitepeak Telecom,Wrong Name,1\n"
        b"2026-10,R99,,,1\n"
    )
    result = await upload(env, content)
    assert codes(result) == {"customer_mismatch", "project_mismatch", "unknown_project"}


async def test_customer_level_and_project_level_actuals_cannot_both_be_loaded(env: Env) -> None:
    both = csv_file(
        "2026-10,Kestrel Life,,500",
        "2026-10,Kestrel Life,Policy Admin,500",
    )
    assert codes(await upload(env, both)) == {"level_overlap"}

    assert (await upload(env, csv_file("2026-10,Kestrel Life,,500"))).status == "completed"
    later = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,500"))
    assert codes(later) == {"level_overlap"}  # clashes with what is already on record
    other_month = await upload(env, csv_file("2026-11,Kestrel Life,Policy Admin,500"))
    assert other_month.status == "completed"  # a different month is a different key


async def test_dry_run_reconciles_without_storing(env: Env) -> None:
    result = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,1000"), dry_run=True)
    assert result.status == "completed" and result.dry_run and result.upload is None
    assert result.report["months"][0]["actual_total"] == "1000.00"
    assert await active(env.session) == []
    assert await env.session.scalar(select(func.count()).select_from(ActualsUpload)) == 0

    bad = await upload(env, csv_file("2026-10,Nobody,,1"), dry_run=True)
    assert (
        bad.status == "rejected"
        and await env.session.scalar(select(func.count()).select_from(ActualsUpload)) == 0
    )


# ---- re-uploading ---------------------------------------------------------------------------


async def test_reuploading_the_same_file_changes_nothing(env: Env) -> None:
    content = csv_file(
        "2026-10,Kestrel Life,Policy Admin,1000", "2026-10,Initech,Existing Work,500"
    )
    first = await upload(env, content)
    second = await upload(env, content)

    assert (first.report["rows_new"], second.report["rows_new"]) == (2, 0)
    assert second.report["rows_unchanged"] == 2 and second.report["rows_replaced"] == 0
    assert len(await active(env.session)) == 2
    assert await env.session.scalar(select(func.count()).select_from(MonthlyActual)) == 2
    assert await env.session.scalar(select(func.count()).select_from(ActualsUpload)) == 2


async def test_a_corrected_value_supersedes_the_old_one_and_keeps_it(env: Env) -> None:
    await upload(
        env, csv_file("2026-10,Kestrel Life,Policy Admin,1000", "2026-10,Initech,Existing Work,500")
    )
    result = await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,1100"))

    assert (result.report["rows_replaced"], result.report["rows_new"]) == (1, 0)
    rows = list(await env.session.scalars(select(MonthlyActual).order_by(MonthlyActual.id)))
    assert [(r.amount, r.superseded_at is not None) for r in rows] == [
        (Decimal("1000.00"), True),
        (Decimal("500.00"), False),
        (Decimal("1100.00"), False),
    ]
    assert rows[0].superseded_by_upload_id == result.upload.id if result.upload else False
    assert sum(r.amount for r in await active(env.session)) == Decimal(
        "1600.00"
    )  # Initech untouched


async def test_a_partial_file_for_a_month_does_not_remove_other_customers(env: Env) -> None:
    await upload(
        env, csv_file("2026-10,Kestrel Life,Policy Admin,1000", "2026-10,Initech,Existing Work,500")
    )
    await upload(env, csv_file("2026-10,Kestrel Life,Policy Admin,1000"))
    assert len(await active(env.session)) == 2
