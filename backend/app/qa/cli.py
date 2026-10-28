"""Command line: `uv run python -m app.qa nightly|run|findings` (see --help)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime

from sqlalchemy import select

from app.config import get_settings
from app.models import QaFinding
from app.qa.engine import QaBusyError, run_qa_safely
from app.qa.jobs import run_nightly

EXIT_OK, EXIT_FAILED = 0, 1


async def _run(nightly: bool, as_of: date | None = None) -> int:
    from app.database import async_session_factory, engine

    settings = get_settings()
    try:
        async with async_session_factory() as session:
            try:
                if nightly:
                    result = await run_nightly(session, settings)
                else:
                    now = (
                        datetime(as_of.year, as_of.month, as_of.day, 9, tzinfo=UTC)
                        if as_of
                        else None
                    )
                    result = await run_qa_safely(session, settings, trigger="manual", now=now)
            except QaBusyError as exc:
                print(f"[skipped] {exc}")
                return EXIT_OK
    finally:
        await engine.dispose()
    if result.status != "completed":
        print(f"QA run #{result.run_id} FAILED: {result.error}")
        return EXIT_FAILED
    print(
        f"QA run #{result.run_id}: {result.new} new, {result.seen} still open or decided, "
        f"{result.reopened} reopened, {result.resolved} resolved, {result.open_total} open."
    )
    return EXIT_OK


async def _findings() -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            for f in await session.scalars(select(QaFinding).order_by(QaFinding.id)):
                print(f"#{f.id:<4} {f.status:<9} {f.severity:<7} {f.rule_code:<20} {f.title}")
    finally:
        await engine.dispose()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.qa",
        description="Deterministic QA checks. Findings are for humans to accept or dismiss; "
        "these commands never change revenue.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "nightly",
        help="the scheduled job: run every check and record the outcome (safe to repeat)",
    )
    run = commands.add_parser("run", help="run every check now")
    run.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="judge staleness as of this date (YYYY-MM-DD) instead of today; for demos with the "
        "dummy data, whose rows were last updated in September 2026",
    )
    commands.add_parser("findings", help="list the findings")
    args = parser.parse_args(argv)
    if args.command == "findings":
        return asyncio.run(_findings())
    return asyncio.run(_run(args.command == "nightly", getattr(args, "as_of", None)))
