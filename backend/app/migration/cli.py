"""Command line entry point: `uv run python -m app.migration --help`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.fiscal import fiscal_year
from app.migration.loader import ImportReport, import_workbook
from app.migration.workbook import ParsedWorkbook, WorkbookStructureError, parse_workbook

DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parents[3] / "reference/03-demo-data/Chosen_One_Dummy_Data_FY27.xlsx"
)
EXIT_OK, EXIT_FAILED, EXIT_UNRECONCILED = 0, 1, 2


def _print_issues(parsed: ParsedWorkbook) -> None:
    rejected = [i for i in parsed.issues if i.row_rejected]
    warnings = [i for i in parsed.issues if not i.row_rejected]
    print(f"\nRejected rows ({parsed.rejected_row_count}):")
    for issue in rejected:
        print(
            f"  row {issue.row_number} {issue.source_row_id or '-'} [{issue.code}] {issue.message}"
        )
    if not rejected:
        print("  none")
    print(f"\nWarnings ({len(warnings)}) - imported, but worth a look:")
    for issue in warnings:
        print(
            f"  row {issue.row_number} {issue.source_row_id or '-'} [{issue.code}] {issue.message}"
        )
    if not warnings:
        print("  none")


def _print_reconciliation(report: ImportReport) -> None:
    print("\nMonthly reconciliation (workbook source total vs database total):")
    print(f"  {'Month':<9}{'FY':<6}{'Source':>14}{'Database':>14}{'Excel cached':>14}  Result")
    for row in report.reconciliation:
        excel = "n/a" if row.excel_reported_total is None else f"{row.excel_reported_total:,.2f}"
        print(
            f"  {row.month:%b %Y}  {fiscal_year(row.month):<6}{row.source_total:>14,.2f}"
            f"{row.database_total:>14,.2f}{excel:>14}  {'OK' if row.matches else 'VARIANCE'}"
        )


def run(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"Workbook not found: {path}", file=sys.stderr)
        return EXIT_FAILED
    try:
        parsed = parse_workbook(path)
    except WorkbookStructureError as exc:
        print(f"Cannot import: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"Workbook: {path.name}  (sha256 {parsed.sha256[:12]}..., as of {parsed.as_of})")
    print(
        f"Read {parsed.rows_read} project rows ({parsed.blank_rows} blank rows skipped), "
        f"{len(parsed.month_columns)} fiscal months "
        f"{parsed.month_columns[0]:%b %Y} - {parsed.month_columns[-1]:%b %Y}, "
        f"{len(parsed.users)} users."
    )

    if args.dry_run:
        print("DRY RUN: nothing written to the database.")
        print(f"Would import {len(parsed.projects)} projects.")
        _print_issues(parsed)
        return EXIT_FAILED if parsed.rejected_row_count and args.strict else EXIT_OK

    # Imported here so --help and --dry-run work without a database connection.
    from app.database import async_session_factory, engine

    async def execute() -> ImportReport:
        try:
            async with async_session_factory() as session:
                return await import_workbook(session, parsed)
        finally:
            await engine.dispose()

    try:
        report = asyncio.run(execute())
    except Exception as exc:
        print(f"Import failed and was rolled back: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(
        f"\nImport run #{report.run_id}: {report.projects_created} created, "
        f"{report.projects_updated} updated, {report.projects_unchanged} unchanged, "
        f"{report.rows_rejected} rejected, {report.warnings} warnings."
    )
    _print_issues(parsed)
    _print_reconciliation(report)
    if report.reconciled:
        print("\nRECONCILED: every month matches the workbook to the cent.")
        return EXIT_OK
    print("\nNOT RECONCILED: see VARIANCE rows above (rejected rows are the usual cause).")
    return EXIT_UNRECONCILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.migration",
        description="Import the Chosen One Excel workbook into PostgreSQL. Safe to repeat: "
        "projects are matched on the workbook Row ID, so re-running never duplicates data. "
        "The workbook is only ever read.",
        epilog="Exit codes: 0 imported and reconciled, 1 failed, 2 imported but not reconciled.",
    )
    parser.add_argument("--file", default=str(DEFAULT_WORKBOOK), help="path to the .xlsx workbook")
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and validate only; do not touch the database"
    )
    parser.add_argument(
        "--strict", action="store_true", help="with --dry-run, exit 1 if any row is rejected"
    )
    return run(parser.parse_args(argv))
