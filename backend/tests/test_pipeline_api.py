"""Pipeline API: combined totals and filters, deals, stages, sync and draft confirmation."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api import pipeline as pipeline_api
from app.models import (
    DraftConfirmation,
    HubspotSyncRun,
    PipelineEvent,
    Project,
    ProjectMonthlyRevenue,
    RevenueRevision,
)
from app.pipeline import drafts as drafts_module
from app.pipeline.source import SourceError, SourceLink
from app.pipeline.sync import run_sync
from app.pipeline.weighting import add_months
from app.provisioning import grant
from tests.conftest import SignedIn
from tests.helpers import make_settings
from tests.pipeline_helpers import FakeSource, sdeal, snapshot
from tests.test_import import run_import
from tests.workbook_builder import WorkbookBuilder

ZERO = "0.00"
PEOPLE: list[tuple[str | None, str, list[str], str | None]] = [
    ("AC", "avery@corp.test", ["Client Partner"], None),
    ("JM", "jordan@corp.test", ["Client Partner"], None),
    ("Shah", "riley@corp.test", ["Seller / Account Executive"], None),
    ("AF", "alex@corp.test", ["Commercial Owner"], None),
    (None, "cro@corp.test", ["CRO / Sales Leadership"], "Casey CRO"),
    (None, "admin@corp.test", ["Administrator"], "Ada Admin"),
    (None, "fin@corp.test", ["Finance"], "Fin Ance"),
    (None, "res@corp.test", ["Delivery / Resourcing"], "Re Source"),
    (None, "sam@corp.test", ["Client Partner"], "Sam NotOwner"),
]


@dataclass
class Env:
    session: AsyncSession
    source: FakeSource

    async def load(
        self, deals: list[Any], links: list[SourceLink] | None = None, now: datetime | None = None
    ) -> None:
        self.source.current = snapshot(deals, links=links)
        report = await run_sync(
            self.session, self.source, make_settings(), now=now or datetime.now(UTC)
        )
        assert report.status == "completed", report.error


@pytest_asyncio.fixture
async def env(session: AsyncSession, builder: WorkbookBuilder) -> Env:
    builder.months = [add_months(date(2026, 10, 1), i) for i in range(12)]
    builder.add(
        "R1",
        Customer="Kestrel Life",
        Project="Policy Admin",
        Industry="Insurance",
        amounts=[1000.0, 2000.0, 3000.0],
    )
    builder.add(
        "R2",
        Customer="Granitepeak Telecom",
        Project="MuleSoft API Program",
        **{"Client Partner": "JM"},
        Industry="Broad Markets",
        amounts=[1000.0, 2000.0, 3000.0],
    )
    builder.add(
        "R3",
        Customer="Initech",
        Project="Existing Work",
        CATG="EN",
        TECH="CRM",
        Region="UK",
        Industry="Banking & FS",
        amounts=[1000.0, 2000.0, 3000.0],
    )
    await run_import(session, builder)
    for code, email, roles, name in PEOPLE:
        await grant(session, email=email, roles=roles, code=code, name=name)
    return Env(session, FakeSource())


async def get(http: AsyncClient, path: str, **params: Any) -> Any:
    response = await http.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_month(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["month"]: m for m in body["months"]}


def money(value: str) -> Decimal:
    return Decimal(value)


# ---- access ---------------------------------------------------------------------------------


READ_PATHS = [
    "/v1/pipeline/deals",
    "/v1/pipeline/deals/1",
    "/v1/forecast/monthly",
    "/v1/pipeline/stages",
    "/v1/pipeline/filter-options",
    "/v1/pipeline/sync/status",
    "/v1/pipeline/confirmations",
]


async def test_every_pipeline_endpoint_requires_sign_in(client: AsyncClient) -> None:
    for path in READ_PATHS:
        assert (await client.get(path)).status_code == 401, path
    assert (await client.post("/v1/pipeline/sync")).status_code == 401
    assert (await client.put("/v1/pipeline/stages/1", json={})).status_code == 401
    assert (await client.post("/v1/pipeline/drafts/1/confirm", json={})).status_code == 401


async def test_mutations_need_the_csrf_token(env: Env, as_user: SignedIn) -> None:
    http, _ = await as_user("admin@corp.test")
    for method, path, body in (
        ("post", "/v1/pipeline/sync", None),
        ("put", "/v1/pipeline/stages/1", {"probability": 0.5}),
        ("post", "/v1/pipeline/drafts/1/confirm", {}),
    ):
        response = await getattr(http, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403, path
        assert response.json()["detail"]["code"] == "csrf_failed"


@pytest.mark.parametrize(
    "email", ["fin@corp.test", "res@corp.test", "sam@corp.test", "avery@corp.test"]
)
async def test_every_signed_in_role_can_read_the_pipeline(
    env: Env, as_user: SignedIn, email: str
) -> None:
    await env.load([sdeal("1")])
    http, _ = await as_user(email)
    for path in READ_PATHS[:1] + READ_PATHS[2:]:
        assert (await http.get(path)).status_code == 200, path


async def test_empty_pipeline_endpoints_answer_cleanly(env: Env, as_user: SignedIn) -> None:
    http, _ = await as_user("fin@corp.test")
    assert (await get(http, "/v1/pipeline/deals"))["items"] == []
    assert (await get(http, "/v1/pipeline/confirmations")) == []
    totals = await get(http, "/v1/forecast/monthly")
    assert totals["weighted_total"] == ZERO and totals["committed_total"] != ZERO
    assert (await http.get("/v1/pipeline/deals/999")).status_code == 404


# ---- combined forecast ----------------------------------------------------------------------


async def test_committed_weighted_and_combined_totals_by_month(env: Env, as_user: SignedIn) -> None:
    await env.load([sdeal("1")])  # PRD example: $120k at 50% from Nov for 6 months
    http, _ = await as_user("fin@corp.test")
    body = await get(http, "/v1/forecast/monthly")

    months = by_month(body)
    assert (months["2026-10-01"]["committed"], months["2026-10-01"]["weighted"]) == (
        "3000.00",
        ZERO,
    )
    assert (
        months["2026-11-01"]["committed"] == "6000.00"
        and months["2026-11-01"]["weighted"] == "10000.00"
    )
    assert months["2026-11-01"]["total"] == "16000.00"
    assert (
        months["2027-04-01"]["weighted"] == "10000.00" and months["2027-05-01"]["weighted"] == ZERO
    )
    assert body["weighted_total"] == "60000.00" and body["committed_total"] == "18000.00"
    assert body["combined_total"] == "78000.00" and body["weighted_deals"] == 1
    assert sum(money(m["total"]) for m in body["months"]) == money(body["combined_total"])
    # Fiscal years run April to March: the April 2027 instalment belongs to FY28.
    assert [(y["fiscal_year"], y["total"]) for y in body["fiscal_years"]] == [
        ("FY27", "68000.00"),
        ("FY28", "10000.00"),
    ]
    for month in body["months"]:
        assert money(month["total"]) == money(month["committed"]) + money(month["weighted"])


async def test_only_contributing_deals_are_weighted(env: Env, as_user: SignedIn) -> None:
    await env.load(
        [
            sdeal("1"),  # counts: 60,000
            sdeal("2", stage="Closed Lost"),
            sdeal("3", stage="Qualified"),  # excluded
            sdeal("4", start=None, duration=None),  # counts as an amount but cannot be phased
        ]
    )
    http, _ = await as_user("fin@corp.test")
    body = await get(http, "/v1/forecast/monthly")

    assert body["weighted_total"] == "60000.00" and body["weighted_deals"] == 2
    assert body["unphased"] == {"deals": 1, "weighted_amount": "60000.00"}  # shown, not guessed


async def test_amounts_outside_the_forecast_window_are_reported_not_lost(
    env: Env, as_user: SignedIn
) -> None:
    await env.load(
        [sdeal("1", start=date(2027, 8, 1), duration=6, amount="120000")]
    )  # window ends Sep 2027
    http, _ = await as_user("fin@corp.test")
    body = await get(http, "/v1/forecast/monthly")
    assert body["weighted_total"] == "20000.00"  # Aug and Sep 2027, 10,000 each
    assert body["outside_window_weighted"] == "40000.00"  # Oct 2027 to Jan 2028


async def test_a_linked_deal_is_never_counted_as_weighted(env: Env, as_user: SignedIn) -> None:
    """FR-14/FR-15: a deal represented by committed revenue has no weighted line."""
    await env.load(
        [sdeal("1", company="Granitepeak Telecom", stage="Closed Won"), sdeal("2")],
        links=[SourceLink("1", "R2")],
    )
    http, _ = await as_user("fin@corp.test")
    body = await get(http, "/v1/forecast/monthly")
    assert body["weighted_total"] == "60000.00"  # only deal 2
    deals = {d["hubspot_deal_id"]: d for d in (await get(http, "/v1/pipeline/deals"))["items"]}
    assert deals["1"]["status"] == "linked" and deals["1"]["weighted_amount"] == ZERO
    assert deals["1"]["linked_project"]["source_row_id"] == "R2"


async def test_drafts_are_not_committed_but_the_deal_counts_at_one_hundred_percent(
    env: Env, as_user: SignedIn
) -> None:
    await env.load(
        [
            sdeal(
                "1",
                stage="Closed Won",
                company="Brightharbor Assurance",
                amount="60000",
                duration=6,
            )
        ]
    )
    http, _ = await as_user("fin@corp.test")
    body = await get(http, "/v1/forecast/monthly")

    assert body["committed_total"] == "18000.00"  # the draft's schedule is NOT in committed
    assert body["weighted_total"] == "60000.00"  # the won deal counts, once, at 100%
    assert body["combined_total"] == "78000.00"
    old = await get(http, "/v1/revenue/monthly-totals")
    assert old["grand_total"] == "18000.00"  # the committed endpoint agrees


async def test_confirming_a_draft_moves_the_deal_from_weighted_to_committed_counting_it_once(
    env: Env, as_user: SignedIn
) -> None:
    await env.load(
        [
            sdeal(
                "1",
                stage="Closed Won",
                company="Brightharbor Assurance",
                amount="60000",
                duration=6,
            )
        ]
    )
    http, headers = await as_user("riley@corp.test")  # the deal owner (AE)
    before = await get(http, "/v1/forecast/monthly")
    draft = (await get(http, "/v1/pipeline/confirmations"))[0]

    response = await http.post(
        f"/v1/pipeline/drafts/{draft['project_id']}/confirm", json={}, headers=headers
    )
    assert response.status_code == 200, response.text
    after = await get(http, "/v1/forecast/monthly")

    assert before["combined_total"] == after["combined_total"] == "78000.00"  # counted once, always
    assert after["committed_total"] == "78000.00" and after["weighted_total"] == ZERO
    assert after["weighted_deals"] == 0
    for month in after["months"]:
        assert money(month["total"]) == money(month["committed"]) + money(month["weighted"])


async def test_filters_apply_to_committed_and_weighted_alike(env: Env, as_user: SignedIn) -> None:
    await env.load(
        [
            sdeal("1", company="Kestrel Life", practice="PEGA", region="UK", owner="Riley Shah"),
            sdeal("2", company="Somebody New", practice="APPIAN", region="NA", owner="Avery Chen"),
        ]
    )
    http, _ = await as_user("fin@corp.test")

    async def totals(**filters: str) -> dict[str, Any]:
        return await get(http, "/v1/forecast/monthly", **filters)

    everything = await totals()
    assert (everything["committed_total"], everything["weighted_total"]) == (
        "18000.00",
        "120000.00",
    )
    assert not everything["filters_applied"]

    by_practice = await totals(practice="PEGA")  # a deal-only attribute for the weighted side
    assert (by_practice["weighted_total"], by_practice["filters_applied"]) == ("60000.00", True)
    assert by_practice["committed_total"] == ZERO  # no committed project has this TECH

    crm = await totals(practice="CRM")  # committed R3 only
    assert (crm["committed_total"], crm["weighted_total"]) == ("6000.00", ZERO)

    uk = await totals(region="UK")  # R3 and deal 1
    assert (uk["committed_total"], uk["weighted_total"]) == ("6000.00", "60000.00")

    industry = await totals(industry="Insurance")  # R1, and deal 1 via its (existing) customer
    assert (industry["committed_total"], industry["weighted_total"]) == ("6000.00", "60000.00")

    customer = await totals(customer="kestrel life")  # case-insensitive
    assert (customer["committed_total"], customer["weighted_total"]) == ("6000.00", "60000.00")

    owner = await totals(owner="Shah")  # the deal owner, resolved to a known person
    assert (owner["committed_total"], owner["weighted_total"]) == (ZERO, "60000.00")

    category = await totals(category="EN")  # R3; deals have no category until they become projects
    assert (category["committed_total"], category["weighted_total"]) == ("6000.00", ZERO)

    combined = await totals(region="UK", practice="PEGA")
    assert (combined["committed_total"], combined["weighted_total"]) == (ZERO, "60000.00")
    assert (await totals(customer="Nobody"))["combined_total"] == ZERO


async def test_committed_owner_filter_uses_project_ownership(env: Env, as_user: SignedIn) -> None:
    http, _ = await as_user("fin@corp.test")
    assert (await get(http, "/v1/forecast/monthly", owner="JM"))[
        "committed_total"
    ] == "6000.00"  # R2
    assert (await get(http, "/v1/forecast/monthly", owner="AC"))["committed_total"] == "12000.00"


async def test_filter_options_cover_projects_and_deals(env: Env, as_user: SignedIn) -> None:
    await env.load([sdeal("1", company="Somebody New", practice="CREATIO", region="IND")])
    http, _ = await as_user("fin@corp.test")
    options = await get(http, "/v1/pipeline/filter-options")
    assert {"CREATIO", "APPIAN", "CRM"} <= set(options["practices"])
    assert {"IND", "UK", "NA"} <= set(options["regions"])
    assert options["categories"] == ["EE", "EN", "NN"] and "Somebody New" in options["customers"]
    assert {"code": "Shah", "name": "Riley Shah"} in options["owners"]
    assert "Insurance" in options["industries"]


# ---- deals ---------------------------------------------------------------------------------


async def test_deal_list_filters_sorts_and_paginates(env: Env, as_user: SignedIn) -> None:
    await env.load(
        [
            sdeal("1", amount="100000"),
            sdeal("2", amount="300000"),
            sdeal("3", stage="Closed Lost"),
            sdeal("4", amount="200000", stage="Contracting"),
        ]
    )
    http, _ = await as_user("fin@corp.test")

    ranked = await get(http, "/v1/pipeline/deals", status="counting")
    assert [d["hubspot_deal_id"] for d in ranked["items"]] == ["4", "2", "1"]  # by weighted amount
    assert ranked["weighted_total"] == "360000.00" and ranked["total_items"] == 3
    assert (await get(http, "/v1/pipeline/deals", status="closed_lost"))["total_items"] == 1
    assert [
        d["hubspot_deal_id"]
        for d in (await get(http, "/v1/pipeline/deals", sort="amount"))["items"]
    ][:2] == ["2", "4"]
    page = await get(http, "/v1/pipeline/deals", page_size=2, page=2, status="counting")
    assert (page["total_pages"], [d["hubspot_deal_id"] for d in page["items"]]) == (2, ["1"])
    assert (await http.get("/v1/pipeline/deals", params={"page_size": 501})).status_code == 422
    assert (await http.get("/v1/pipeline/deals", params={"sort": "nonsense"})).status_code == 422


async def test_deal_detail_shows_phasing_findings_and_the_event_trail(
    env: Env, as_user: SignedIn
) -> None:
    await env.load([sdeal("1", close=datetime(2026, 2, 1, tzinfo=UTC))])
    await env.load([sdeal("1", close=datetime(2026, 2, 1, tzinfo=UTC), stage="Contracting")])
    http, _ = await as_user("fin@corp.test")
    deal_id = (await get(http, "/v1/pipeline/deals"))["items"][0]["id"]

    detail = await get(http, f"/v1/pipeline/deals/{deal_id}")
    assert detail["stage"] == "Contracting" and detail["probability"] == "0.8000"
    assert detail["weighted_amount"] == "96000.00"
    assert [m["amount"] for m in detail["monthly"]] == ["16000.00"] * 6
    assert "stale_close_date" in [f["code"] for f in detail["findings_list"]]
    assert [e["event_type"] for e in detail["events"]] == ["stage_changed", "deal_entered_forecast"]
    assert detail["can_confirm"] is False and detail["schedule_check"] is None


# ---- stages ---------------------------------------------------------------------------------


async def test_an_administrator_can_change_a_stage_probability_and_it_is_audited(
    env: Env, as_user: SignedIn, session: AsyncSession
) -> None:
    await env.load([sdeal("1")])
    http, headers = await as_user("admin@corp.test")
    stages = {s["name"]: s for s in await get(http, "/v1/pipeline/stages")}
    stage = stages["Negotiation/Review"]
    assert stage["probability"] == "0.5000" and "fake" in stage["source"]

    response = await http.put(
        f"/v1/pipeline/stages/{stage['id']}", json={"probability": 0.75}, headers=headers
    )
    assert response.status_code == 200 and response.json()["probability"] == "0.7500"

    assert (await get(http, "/v1/forecast/monthly"))[
        "weighted_total"
    ] == "90000.00"  # 120,000 x 75%
    session.expire_all()
    (event,) = list(
        await session.scalars(
            select(PipelineEvent).where(PipelineEvent.event_type == "stage_updated")
        )
    )
    assert event.actor_label == "Ada Admin" and "0.5000 -> 0.7500" in event.message

    again = await http.put(
        f"/v1/pipeline/stages/{stage['id']}", json={"probability": "0.75"}, headers=headers
    )
    assert again.status_code == 200
    session.expire_all()
    assert (
        len(
            list(
                await session.scalars(
                    select(PipelineEvent).where(PipelineEvent.event_type == "stage_updated")
                )
            )
        )
        == 1
    )


async def test_the_forecast_stage_rule_can_be_changed_for_open_stages_only(
    env: Env, as_user: SignedIn
) -> None:
    await env.load([sdeal("1", stage="Presentation")])
    http, headers = await as_user("admin@corp.test")
    stages = {s["name"]: s for s in await get(http, "/v1/pipeline/stages")}
    off = await http.put(
        f"/v1/pipeline/stages/{stages['Presentation']['id']}",
        json={"in_forecast": False},
        headers=headers,
    )
    assert off.status_code == 200
    closed = await http.put(
        f"/v1/pipeline/stages/{stages['Closed Lost']['id']}",
        json={"in_forecast": True},
        headers=headers,
    )
    assert closed.status_code == 422 and closed.json()["detail"]["code"] == "stage_not_open"
    # The rule applies from the next sync: the deal is then excluded as below the forecast stage.
    await env.load([sdeal("1", stage="Presentation")])
    assert (await get(http, "/v1/pipeline/deals"))["items"][0]["lifecycle"] == "excluded"


@pytest.mark.parametrize(
    "email", ["cro@corp.test", "fin@corp.test", "avery@corp.test", "res@corp.test"]
)
async def test_only_administrators_can_change_stages(
    env: Env, as_user: SignedIn, email: str
) -> None:
    await env.load([sdeal("1")])
    http, headers = await as_user(email)
    stage_id = (await get(http, "/v1/pipeline/stages"))[0]["id"]
    response = await http.put(
        f"/v1/pipeline/stages/{stage_id}", json={"probability": 0.9}, headers=headers
    )
    assert response.status_code == 403 and response.json()["detail"]["code"] == "admin_only"


@pytest.mark.parametrize(
    "bad", [-0.1, 1.1, "abc", True, 0.12345, "NaN", "Infinity", [0.5], {"a": 1}]
)
async def test_invalid_probabilities_are_rejected(env: Env, as_user: SignedIn, bad: object) -> None:
    await env.load([sdeal("1")])
    http, headers = await as_user("admin@corp.test")
    stage = (await get(http, "/v1/pipeline/stages"))[3]
    response = await http.put(
        f"/v1/pipeline/stages/{stage['id']}", json={"probability": bad}, headers=headers
    )
    assert response.status_code == 422, bad
    assert (await get(http, "/v1/pipeline/stages"))[3]["probability"] == stage["probability"]


async def test_unknown_stage_is_a_404(env: Env, as_user: SignedIn) -> None:
    http, headers = await as_user("admin@corp.test")
    assert (
        await http.put("/v1/pipeline/stages/9999", json={"probability": 0.5}, headers=headers)
    ).status_code == 404


# ---- sync: status and trigger --------------------------------------------------------------


async def test_sync_status_reports_never_ok_old_and_failed(
    env: Env, as_user: SignedIn, session: AsyncSession
) -> None:
    http, _ = await as_user("cro@corp.test")
    never = await get(http, "/v1/pipeline/sync/status")
    assert (never["stale"], never["reason"], never["last_run"]) == (True, "never", None)

    await env.load([sdeal("1")])
    ok = await get(http, "/v1/pipeline/sync/status")
    assert (ok["stale"], ok["reason"]) == (False, None) and ok["last_success"]["deals_seen"] == 1
    assert (ok["can_sync"], ok["configured_source"], ok["stale_after_minutes"]) == (
        True,
        "workbook",
        60,
    )

    await session.execute(
        update(HubspotSyncRun).values(finished_at=datetime.now(UTC) - timedelta(hours=2))
    )
    await session.commit()
    old = await get(http, "/v1/pipeline/sync/status")
    assert (old["stale"], old["reason"]) == (True, "old") and old["stale_since"]

    env.source.error = SourceError("HubSpot is down")
    await run_sync(session, env.source, make_settings(), now=datetime.now(UTC))
    failed = await get(http, "/v1/pipeline/sync/status")
    assert (failed["stale"], failed["reason"]) == (True, "failed")
    assert (
        failed["last_run"]["status"] == "failed"
        and failed["last_run"]["error_message"] == "HubSpot is down"
    )
    assert failed["last_success"]["status"] == "completed"  # the last good data is still known


@pytest.mark.parametrize("email", ["cro@corp.test", "admin@corp.test"])
async def test_leadership_and_admins_can_sync_on_demand(
    env: Env, as_user: SignedIn, monkeypatch: pytest.MonkeyPatch, email: str
) -> None:
    env.source.current = snapshot([sdeal("1"), sdeal("2")])
    monkeypatch.setattr(pipeline_api, "build_source", lambda *_args: env.source)
    http, headers = await as_user(email)

    response = await http.post("/v1/pipeline/sync", headers=headers)

    assert response.status_code == 200, response.text
    run = response.json()
    assert (run["status"], run["deals_seen"], run["deals_created"]) == ("completed", 2, 2)
    assert run["triggered_by_label"] in {"Casey CRO", "Ada Admin"}
    assert (await get(http, "/v1/pipeline/deals"))["total_items"] == 2


@pytest.mark.parametrize(
    "email", ["avery@corp.test", "riley@corp.test", "fin@corp.test", "res@corp.test"]
)
async def test_other_roles_cannot_trigger_a_sync(
    env: Env, as_user: SignedIn, monkeypatch: pytest.MonkeyPatch, email: str
) -> None:
    monkeypatch.setattr(pipeline_api, "build_source", lambda *_a: env.source)
    http, headers = await as_user(email)
    response = await http.post("/v1/pipeline/sync", headers=headers)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "sync_not_permitted"
    assert env.source.fetches == 0
    assert (await get(http, "/v1/pipeline/sync/status"))["can_sync"] is False


async def test_a_failed_sync_answers_502_and_the_forecast_keeps_working(
    env: Env, as_user: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.load([sdeal("1")])
    http, headers = await as_user("cro@corp.test")
    before = await get(http, "/v1/forecast/monthly")
    monkeypatch.setattr(
        pipeline_api,
        "build_source",
        lambda *_a: FakeSource(error=SourceError("HubSpot unreachable")),
    )

    response = await http.post("/v1/pipeline/sync", headers=headers)

    assert response.status_code == 502
    assert (
        response.json()["detail"]["code"] == "sync_failed"
        and "unreachable" in response.json()["detail"]["message"]
    )
    assert await get(http, "/v1/forecast/monthly") == before  # forecast unchanged and available
    assert (await get(http, "/v1/pipeline/deals"))["total_items"] == 1
    status = await get(http, "/v1/pipeline/sync/status")
    assert (status["stale"], status["reason"]) == (True, "failed")


async def test_the_real_workbook_source_failing_is_handled_like_any_other(
    env: Env, as_user: SignedIn, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.config import get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: make_settings(
        hubspot_workbook_path=str(tmp_path / "gone.xlsx")
    )
    http, headers = await as_user("cro@corp.test")
    response = await http.post("/v1/pipeline/sync", headers=headers)
    assert response.status_code == 502 and "Cannot read" in response.json()["detail"]["message"]


async def test_two_syncs_cannot_run_at_once(
    env: Env, as_user: SignedIn, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.source.current = snapshot([sdeal("1")])
    monkeypatch.setattr(pipeline_api, "build_source", lambda *_a: env.source)
    http, headers = await as_user("cro@corp.test")
    async with engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": pipeline_api.SYNC_LOCK_KEY})
        busy = await http.post("/v1/pipeline/sync", headers=headers)
        await holder.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": pipeline_api.SYNC_LOCK_KEY}
        )
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "sync_in_progress"
    assert env.source.fetches == 0
    assert (await http.post("/v1/pipeline/sync", headers=headers)).status_code == 200


# ---- draft confirmation ---------------------------------------------------------------------


async def draft_for(env: Env, **deal: Any) -> int:
    """Create a Closed Won draft and return its project id."""
    base: dict[str, Any] = {
        "stage": "Closed Won",
        "company": "Brightharbor Assurance",
        "amount": "60000",
        "name": "Brightharbor - Claims AI Pilot",
    }
    base.update(deal)
    await env.load([sdeal("10", **base)])
    project = await env.session.scalar(select(Project).where(Project.source_row_id == "HS-10"))
    assert project is not None
    return project.id


async def test_the_owner_confirms_the_draft_and_everything_is_audited(
    env: Env, as_user: SignedIn, session: AsyncSession
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user("riley@corp.test")
    listed = await get(http, "/v1/pipeline/confirmations")
    assert [(c["project_id"], c["owner_name"], c["can_confirm"]) for c in listed] == [
        (project_id, "Riley Shah", True)
    ]
    assert listed[0]["schedule_total"] == "60000.00" and listed[0]["amount"] == "60000.0000"

    response = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm",
        json={"category": "NN", "industry": "Insurance"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["weighted_line_retired"]) == ("Active", True)
    assert (
        body["schedule_check"]["exceeds_threshold"] is False
        and body["schedule_check"]["variance_ratio"] == "0.0000"
    )
    session.expire_all()
    project = await session.get(Project, project_id)
    assert project is not None and (project.status, project.category_code, project.industry) == (
        "Active",
        "NN",
        "Insurance",
    )
    confirmation = await session.scalar(select(DraftConfirmation))
    assert (
        confirmation is not None
        and confirmation.confirmed_at is not None
        and confirmation.confirmed_by_id
    )
    kinds = [
        e.event_type
        for e in await session.scalars(select(PipelineEvent).order_by(PipelineEvent.id))
    ]
    assert kinds[-2:] == ["draft_confirmed", "weighted_line_retired"]
    changes = list(
        await session.scalars(
            select(RevenueRevision)
            .where(
                RevenueRevision.project_id == project_id,
                RevenueRevision.field != "revenue",
                RevenueRevision.actor_user_id.is_not(None),
            )
            .order_by(RevenueRevision.id)
        )
    )
    assert [(c.field, c.old_value, c.new_value, c.actor_label) for c in changes] == [
        ("status", "Draft", "Active", "Riley Shah"),
        ("category", None, "NN", "Riley Shah"),
        ("industry", None, "Insurance", "Riley Shah"),
    ]
    assert await get(http, "/v1/pipeline/confirmations") == []
    history = await get(http, f"/v1/projects/{project_id}/history")
    assert "status" in [
        i["field"] for i in history["items"]
    ]  # visible in the project's change history


@pytest.mark.parametrize(
    ("email", "code"),
    [
        ("sam@corp.test", "not_project_owner"),
        ("avery@corp.test", "not_project_owner"),
        ("fin@corp.test", "read_only_role"),
        ("res@corp.test", "read_only_role"),
    ],
)
async def test_people_who_do_not_own_the_draft_cannot_confirm_it(
    env: Env, as_user: SignedIn, session: AsyncSession, email: str, code: str
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user(email)
    response = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == code and "Riley Shah" in detail["message"]  # the owner is named
    session.expire_all()
    project = await session.get(Project, project_id)
    assert project is not None and project.status == "Draft"
    assert [
        c["can_confirm"] for c in await get(http, "/v1/pipeline/confirmations", scope="all")
    ] == [False]
    assert (
        await get(http, "/v1/pipeline/confirmations") == []
    )  # nothing awaits *their* confirmation


@pytest.mark.parametrize("email", ["cro@corp.test", "admin@corp.test"])
async def test_leadership_and_admins_can_confirm_any_draft(
    env: Env, as_user: SignedIn, email: str
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user(email)
    assert len(await get(http, "/v1/pipeline/confirmations")) == 1
    assert (
        await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    ).status_code == 200


async def test_a_draft_that_looks_like_an_existing_project_needs_acknowledgement(
    env: Env, as_user: SignedIn
) -> None:
    project_id = await draft_for(env, company="Initech")  # Initech already has "Existing Work" (R3)
    http, headers = await as_user("riley@corp.test")
    listed = (await get(http, "/v1/pipeline/confirmations"))[0]
    assert listed["has_possible_duplicates"] is True

    refused = await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "possible_duplicate"
    assert "Existing Work" in refused.json()["detail"]["message"]

    ok = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm",
        json={"acknowledge_possible_duplicate": True},
        headers=headers,
    )
    assert ok.status_code == 200


async def test_an_empty_schedule_cannot_be_confirmed_until_months_are_entered(
    env: Env, as_user: SignedIn
) -> None:
    project_id = await draft_for(env, start=None, duration=None)  # cannot be phased: no months
    http, headers = await as_user("riley@corp.test")
    refused = await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "empty_schedule"

    entered = await http.put(
        f"/v1/projects/{project_id}/revenue/2026-11-01",
        json={"amount": "5000", "expected_amount": None},
        headers=headers,
    )
    assert entered.status_code == 200  # the owner enters the schedule with the normal editor
    assert (
        await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    ).status_code == 200


async def test_invalid_category_is_rejected_and_nothing_changes(
    env: Env, as_user: SignedIn, session: AsyncSession
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user("riley@corp.test")
    response = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm", json={"category": "ZZ"}, headers=headers
    )
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_category"
    session.expire_all()
    project = await session.get(Project, project_id)
    assert project is not None and project.status == "Draft" and project.category_code is None


async def test_confirming_twice_or_confirming_a_normal_project_is_refused(
    env: Env, as_user: SignedIn
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user("cro@corp.test")
    assert (
        await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    ).status_code == 200
    again = await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers)
    assert again.status_code == 409
    r1 = await env.session.scalar(select(Project).where(Project.source_row_id == "R1"))
    assert r1 is not None
    imported = await http.post(f"/v1/pipeline/drafts/{r1.id}/confirm", json={}, headers=headers)
    assert imported.status_code == 409 and imported.json()["detail"]["code"] == "not_a_draft"
    assert (
        await http.post("/v1/pipeline/drafts/99999/confirm", json={}, headers=headers)
    ).status_code == 404


@pytest.mark.parametrize(
    "body", [{"nonsense": 1}, {"acknowledge_possible_duplicate": "yes"}, {"category": 5}]
)
async def test_confirm_rejects_malformed_requests(
    env: Env, as_user: SignedIn, body: dict[str, Any]
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user("cro@corp.test")
    assert (
        await http.post(f"/v1/pipeline/drafts/{project_id}/confirm", json=body, headers=headers)
    ).status_code == 422


async def test_a_failing_confirmation_leaves_the_draft_and_the_weighted_line_intact(
    env: Env, as_user: SignedIn, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await draft_for(env)
    http, headers = await as_user("riley@corp.test")
    before = await get(http, "/v1/forecast/monthly")

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("event write failed")

    monkeypatch.setattr(drafts_module, "PipelineEvent", boom)
    response = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers
    )
    monkeypatch.undo()

    assert response.status_code == 500
    session.expire_all()
    project = await session.get(Project, project_id)
    assert (
        project is not None and project.status == "Draft"
    )  # status change rolled back with the rest
    assert await get(http, "/v1/forecast/monthly") == before
    confirmation = await session.scalar(select(DraftConfirmation))
    assert confirmation is not None and confirmation.confirmed_at is None


async def test_overdue_drafts_are_flagged_in_the_list_and_the_deal(
    env: Env, as_user: SignedIn
) -> None:
    project_id = await draft_for(env)
    await env.session.execute(
        update(DraftConfirmation).values(due_at=datetime.now(UTC) - timedelta(days=1))
    )
    await env.session.commit()
    http, _ = await as_user("riley@corp.test")
    assert (await get(http, "/v1/pipeline/confirmations"))[0]["overdue"] is True
    deal = (await get(http, "/v1/pipeline/deals", status="awaiting_confirmation"))["items"][0]
    assert deal["draft"]["overdue"] is True and deal["draft"]["project_id"] == project_id


async def test_deal_detail_lets_the_owner_confirm_and_checks_the_schedule(
    env: Env, as_user: SignedIn, session: AsyncSession
) -> None:
    project_id = await draft_for(env, amount="100000", start=date(2027, 8, 1), duration=12)
    http, headers = await as_user("riley@corp.test")
    deal = (await get(http, "/v1/pipeline/deals", status="awaiting_confirmation"))["items"][0]
    detail = await get(http, f"/v1/pipeline/deals/{deal['id']}")

    check = detail["schedule_check"]
    assert detail["can_confirm"] is True
    assert (
        check["outside_window"] != ZERO and check["exceeds_threshold"] is False
    )  # horizon allowed for
    other, _ = await as_user("sam@corp.test")
    assert (await get(other, f"/v1/pipeline/deals/{deal['id']}"))["can_confirm"] is False

    # Cutting the schedule to a tenth of the deal trips the PRD's 10% variance alert.
    await session.execute(
        update(ProjectMonthlyRevenue)
        .where(ProjectMonthlyRevenue.project_id == project_id)
        .values(amount=Decimal("100.00"))
    )
    await session.commit()
    flagged = (await get(http, f"/v1/pipeline/deals/{deal['id']}"))["schedule_check"]
    assert flagged["exceeds_threshold"] is True
    confirmed = await http.post(
        f"/v1/pipeline/drafts/{project_id}/confirm", json={}, headers=headers
    )
    assert (
        confirmed.status_code == 200
        and confirmed.json()["schedule_check"]["exceeds_threshold"] is True
    )


# ---- Milestone 1 and 2 behaviour is unchanged ----------------------------------------------


async def test_committed_endpoints_and_the_audit_log_still_behave(
    env: Env, as_user: SignedIn
) -> None:
    http, headers = await as_user("avery@corp.test")
    r1 = next(p for p in (await get(http, "/v1/projects"))["items"] if p["source_row_id"] == "R1")
    assert r1["can_edit"] is True and r1["total_revenue"] == "6000.00"
    edited = await http.put(
        f"/v1/projects/{r1['id']}/revenue/2026-10-01",
        json={"amount": "1500", "expected_amount": "1000.00"},
        headers=headers,
    )
    assert edited.status_code == 200
    totals = await get(http, "/v1/revenue/monthly-totals")
    baseline = await get(http, "/v1/revenue/monthly-totals", basis="baseline")
    assert totals["months"][0]["total"] == "3500.00" and baseline["months"][0]["total"] == "3000.00"
    assert (await get(http, "/v1/imports/latest"))["reconciled"] is True


async def test_the_pipeline_event_log_is_append_only(env: Env, session: AsyncSession) -> None:
    await env.load([sdeal("1"), sdeal("1", stage="Closed Lost")][:1])
    await env.load([sdeal("1", stage="Closed Lost")])
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("UPDATE pipeline_events SET message = 'tampered'"))
    await session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("DELETE FROM pipeline_events"))
    await session.rollback()
