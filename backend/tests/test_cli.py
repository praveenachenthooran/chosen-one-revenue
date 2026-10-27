import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.migration.cli import EXIT_FAILED, EXIT_OK, EXIT_UNRECONCILED, main
from app.models import Project
from tests.workbook_builder import WorkbookBuilder


def test_missing_file_fails_cleanly(tmp_path: Path) -> None:
    assert main(["--file", str(tmp_path / "nope.xlsx")]) == EXIT_FAILED


def test_dry_run_reports_without_touching_the_database(
    builder: WorkbookBuilder, capsys: pytest.CaptureFixture[str]
) -> None:
    builder.add("R1").add("R2", Status=None)
    assert main(["--file", str(builder.build()), "--dry-run"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "DRY RUN" in output and "missing_required_field" in output
    assert main(["--file", str(builder.path), "--dry-run", "--strict"]) == EXIT_FAILED


async def test_import_command_exit_codes_and_report(
    engine: AsyncEngine,
    session: AsyncSession,
    builder: WorkbookBuilder,
    capsys: pytest.CaptureFixture[str],
) -> None:
    builder.add("R1")
    path = str(builder.build())

    assert await asyncio.to_thread(main, ["--file", path]) == EXIT_OK
    output = capsys.readouterr().out
    assert "1 created" in output and "RECONCILED" in output
    assert await asyncio.to_thread(main, ["--file", path]) == EXIT_OK
    assert "1 unchanged" in capsys.readouterr().out
    assert await session.scalar(select(func.count()).select_from(Project)) == 1

    builder.add("R2", Status=None)  # a rejected row that carries money cannot reconcile
    assert await asyncio.to_thread(main, ["--file", str(builder.build())]) == EXIT_UNRECONCILED
    assert "NOT RECONCILED" in capsys.readouterr().out
