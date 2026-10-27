"""Command line: `uv run python -m app.pipeline sync|stages` (see --help)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.models import HubspotStage
from app.pipeline.source import DEFAULT_WORKBOOK, WorkbookDealSource, build_source
from app.pipeline.sync import SyncReport, run_sync

EXIT_OK, EXIT_FAILED = 0, 1


def _print_report(report: SyncReport) -> None:
    if report.status != "completed":
        print(f"Sync #{report.run_id} FAILED: {report.error}", file=sys.stderr)
        print("The forecast is unchanged: the last good data remains.", file=sys.stderr)
        return
    print(
        f"Sync #{report.run_id} completed from {report.source}: {report.deals_seen} deals "
        f"({report.deals_created} new, {report.deals_updated} updated, "
        f"{report.deals_unchanged} unchanged), {report.drafts_created} draft project(s) created, "
        f"{report.lines_retired} weighted line(s) retired, {len(report.findings)} finding(s)."
    )
    for finding in report.findings:
        who = finding.deal_id or "-"
        print(f"  [{finding.severity}] {finding.code} {who}: {finding.message}")


async def _sync(args: argparse.Namespace) -> int:
    from app.database import async_session_factory, engine

    settings = get_settings()
    if args.file:
        source = WorkbookDealSource(Path(args.file))
    else:
        token = (
            settings.hubspot_access_token.get_secret_value()
            if settings.hubspot_access_token
            else None
        )
        source = build_source(settings.hubspot_source, settings.hubspot_workbook_path, token)
    try:
        async with async_session_factory() as session:
            report = await run_sync(session, source, settings)
    finally:
        await engine.dispose()
    _print_report(report)
    return EXIT_OK if report.status == "completed" else EXIT_FAILED


async def _stages() -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            for stage in await session.scalars(
                select(HubspotStage).order_by(HubspotStage.position)
            ):
                flag = "in forecast" if stage.in_forecast else "excluded"
                print(
                    f"{stage.name:<24} {stage.probability:>6}  {stage.kind:<5} {flag}  "
                    f"[{stage.source}]"
                )
    finally:
        await engine.dispose()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline",
        description="HubSpot pipeline tools. The live HubSpot connection is read-only; this "
        "never writes to HubSpot.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser(
        "sync",
        help="sync deals now (safe to repeat)",
        description="Sync deals from the configured source (HUBSPOT_SOURCE) into PostgreSQL. "
        f"Default workbook: {DEFAULT_WORKBOOK}. Exit code 0 = completed, 1 = failed "
        "(the last good data is kept).",
    )
    sync.add_argument(
        "--file", help="read this HubSpot export workbook instead of the configured source"
    )
    commands.add_parser("stages", help="show the stage probabilities in use")
    args = parser.parse_args(argv)
    return asyncio.run(_sync(args) if args.command == "sync" else _stages())
