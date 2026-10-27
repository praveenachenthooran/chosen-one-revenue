"""Factories for pipeline tests: a fake deal source plus deal and stage builders."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from app.pipeline.source import (
    SourceDeal,
    SourceFinding,
    SourceLink,
    SourceSnapshot,
    SourceStage,
)

AS_OF = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # a Thursday

STAGES = [
    SourceStage("Qualified", Decimal("0.1"), "open", False, 0),  # below the forecast stage
    SourceStage("Presentation", Decimal("0.2"), "open", True, 1),
    SourceStage("Proposal/Price Quote", Decimal("0.4"), "open", True, 2),
    SourceStage("Negotiation/Review", Decimal("0.5"), "open", True, 3),
    SourceStage("Contracting", Decimal("0.8"), "open", True, 4),
    SourceStage("Closed Won", Decimal("1.0"), "won", True, 5),
    SourceStage("Closed Lost", Decimal("0.0"), "lost", False, 6),
]


def sdeal(
    deal_id: str,
    *,
    name: str | None = None,
    stage: str = "Negotiation/Review",
    company: str | None = "Initech",
    company_id: str | None = "9000001",
    owner: str | None = "Riley Shah",
    amount: str | None = "120000",
    start: date | None = date(2026, 11, 1),
    duration: int | None = 6,
    close: datetime | None = datetime(2026, 12, 1, tzinfo=UTC),
    practice: str | None = "APPIAN",
    region: str | None = "NA",
) -> SourceDeal:
    """A deal. Defaults: the PRD acceptance example ($120,000 at 50%, Nov 1, 6 months)."""
    return SourceDeal(
        deal_id=deal_id,
        name=name or f"Deal {deal_id}",
        stage=stage,
        company_name=company,
        company_raw=company,
        company_id=company_id,
        owner_name=owner,
        amount=Decimal(amount) if amount is not None else None,
        close_date=close,
        expected_start=start,
        duration_months=duration,
        practice=practice,
        region=region,
        raw={"Record ID": deal_id},
        sheet="On_Deck",
        row_number=2,
    )


def snapshot(
    deals: list[SourceDeal],
    *,
    links: list[SourceLink] | None = None,
    stages: list[SourceStage] | None = None,
    complete: bool = True,
    findings: list[SourceFinding] | None = None,
) -> SourceSnapshot:
    return SourceSnapshot(
        source="fake",
        source_ref="fake.xlsx",
        as_of=AS_OF,
        deals=deals,
        links=links or [],
        stages=STAGES if stages is None else stages,
        complete=complete,
        findings=findings or [],
    )


@dataclass
class FakeSource:
    """A deal source whose content (or failure) the test controls."""

    current: SourceSnapshot | None = None
    error: Exception | None = None
    name: str = "fake"
    fetches: int = 0

    def fetch(self) -> SourceSnapshot:
        self.fetches += 1
        if self.error is not None:
            raise self.error
        assert self.current is not None
        return self.current
