"""Command line: `uv run python -m app.snapshots scheduled|manual|list` (see --help)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import ForecastSnapshot
from app.snapshots.jobs import run_scheduled_snapshot
from app.snapshots.service import MANUAL, take_snapshot

EXIT_OK, EXIT_FAILED = 0, 1


async def _scheduled() -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            outcome = await run_scheduled_snapshot(session)
    finally:
        await engine.dispose()
    print(f"[{outcome.status}] {outcome.message}")
    return EXIT_FAILED if outcome.status == "failed" else EXIT_OK


async def _manual(note: str | None) -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            snapshot = await take_snapshot(
                session, kind=MANUAL, now=datetime.now(UTC), actor_label="command line", note=note
            )
            await session.commit()
            print(
                f"Snapshot #{snapshot.id} taken ({snapshot.label}): {snapshot.line_count} lines, "
                f"committed {snapshot.committed_total}, weighted {snapshot.weighted_total}, "
                f"combined {snapshot.combined_total}."
            )
    finally:
        await engine.dispose()
    return EXIT_OK


async def _list() -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            rows = await session.scalars(
                select(ForecastSnapshot).order_by(ForecastSnapshot.taken_at.desc())
            )
            for s in rows:
                print(
                    f"#{s.id:<4} {s.taken_at:%Y-%m-%d %H:%M} UTC  {s.kind:<9} "
                    f"combined {s.combined_total:>16}  {s.label}"
                )
    finally:
        await engine.dispose()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.snapshots",
        description="Immutable forecast snapshots. Snapshots can be taken but never changed.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "scheduled",
        help="the monthly job: take this month's snapshot if due and missing (safe to repeat)",
        description="Takes the scheduled snapshot on or after the month's first business day "
        "(Mon-Fri, UTC), once per month. Schedule it daily on weekdays. Exit code 1 = failed.",
    )
    manual = commands.add_parser("manual", help="take an extra snapshot now")
    manual.add_argument("--note", help="why this snapshot is being taken")
    commands.add_parser("list", help="list the snapshots")
    args = parser.parse_args(argv)
    if args.command == "scheduled":
        return asyncio.run(_scheduled())
    if args.command == "manual":
        return asyncio.run(_manual(args.note))
    return asyncio.run(_list())
