"""API tests over a small imported workbook."""

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.main import app
from app.provisioning import grant
from tests.conftest import SignedIn
from tests.helpers import make_settings
from tests.test_import import run_import
from tests.workbook_builder import WorkbookBuilder


@pytest_asyncio.fixture
async def api(
    session: AsyncSession, builder: WorkbookBuilder, client: AsyncClient, as_user: SignedIn
) -> AsyncClient:
    """A signed-in (Sales leadership, so read-everything) client over a small imported workbook."""
    builder.add("R1", Customer="Acme", Project="Portal", amounts=[1000.0, 2000.0, 3000.0])
    builder.add("R2", Customer="Acme", Project="Mobile", AE="Shah", TECH="CRM", Region="UK",
                amounts=[None, 500.5, 500.5])  # fmt: skip
    builder.add("R3", Customer="Globex", Project="Core", Status="Completed", CATG="EN",
                **{"Client Partner": "N/A"}, amounts=[10.0, 20.0, 30.0])  # fmt: skip
    await run_import(session, builder)
    await grant(
        session,
        email="lead@corp.test",
        roles=["CRO / Sales Leadership"],
        code=None,
        name="Lee Lead",
    )
    http, _ = await as_user("lead@corp.test")
    return http


async def test_projects_list_puts_attention_first_and_pages(api: AsyncClient) -> None:
    body = (await api.get("/v1/projects?page_size=2")).json()

    assert (body["total_items"], body["total_pages"], body["page"]) == (3, 2, 1)
    assert [p["source_row_id"] for p in body["items"]] == ["R3", "R2"]  # R3 has no Client Partner
    assert body["items"][0]["attention_count"] == 1
    assert body["items"][0]["monthly"] is None
    page2 = (await api.get("/v1/projects?page_size=2&page=2")).json()
    assert [p["source_row_id"] for p in page2["items"]] == ["R1"]


async def test_projects_list_includes_exact_decimal_strings_and_monthly(
    api: AsyncClient,
) -> None:
    items = (await api.get("/v1/projects?include_revenue=true&sort=customer")).json()["items"]
    mobile = next(p for p in items if p["source_row_id"] == "R2")
    assert mobile["total_revenue"] == "1001.00"
    assert mobile["monthly"] == {"2026-11-01": "500.50", "2026-12-01": "500.50"}
    assert mobile["client_partner"] == {
        "role": "client_partner",
        "code": "AC",
        "name": "Avery Chen",
    }
    assert mobile["ae"]["name"] == "Riley Shah"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("status=Completed", ["R3"]),
        ("practice=CRM", ["R2"]),
        ("region=UK", ["R2"]),
        ("category=EN", ["R3"]),
        ("customer=Acme&sort=project", ["R2", "R1"]),
        ("q=glob", ["R3"]),
        ("q=R2", ["R2"]),
        ("owner=Shah", ["R2"]),
        ("owner=AC", ["R2", "R1"]),
        ("needs_attention=true", ["R3"]),
        ("needs_attention=false&sort=customer", ["R2", "R1"]),
        ("sort=total&descending=true", ["R1", "R2", "R3"]),
    ],
)
async def test_projects_filters_and_sorting(
    api: AsyncClient, query: str, expected: list[str]
) -> None:
    items = (await api.get(f"/v1/projects?{query}")).json()["items"]
    assert [p["source_row_id"] for p in items] == expected


async def test_page_size_is_capped(api: AsyncClient) -> None:
    assert (await api.get("/v1/projects?page_size=501")).status_code == 422
    assert (await api.get("/v1/projects?page=0")).status_code == 422


async def test_project_detail_and_monthly_revenue(api: AsyncClient) -> None:
    listing = (await api.get("/v1/projects?customer=Acme&sort=project")).json()["items"]
    portal_id = next(p["id"] for p in listing if p["name"] == "Portal")

    detail = (await api.get(f"/v1/projects/{portal_id}")).json()
    assert detail["source_row_id"] == "R1" and detail["source_row_number"] == 3
    assert detail["total_revenue"] == "6000.00"
    assert detail["attention"] == []
    assert [o["role"] for o in detail["owners"]] == ["client_partner"]
    first = detail["monthly_revenue"][0]
    assert (first["month"], first["amount"], first["fiscal_year"], first["source_cell"]) == (
        "2026-10-01", "1000.00", "FY27", "Forecast!I3",
    )  # fmt: skip

    revenue = (await api.get(f"/v1/projects/{portal_id}/revenue")).json()
    assert revenue["total"] == "6000.00" and len(revenue["months"]) == 3


async def test_project_detail_lists_attention_reasons(api: AsyncClient) -> None:
    items = (await api.get("/v1/projects?q=R3")).json()["items"]
    detail = (await api.get(f"/v1/projects/{items[0]['id']}")).json()
    assert [a["code"] for a in detail["attention"]] == ["missing_client_partner"]


async def test_unknown_project_is_404(api: AsyncClient) -> None:
    assert (await api.get("/v1/projects/9999")).status_code == 404
    assert (await api.get("/v1/projects/9999/revenue")).status_code == 404


async def test_monthly_and_fiscal_year_totals_are_computed_in_the_backend(
    api: AsyncClient,
) -> None:
    body = (await api.get("/v1/revenue/monthly-totals")).json()
    assert [(m["month"], m["total"], m["project_count"]) for m in body["months"]] == [
        ("2026-10-01", "1010.00", 2),
        ("2026-11-01", "2520.50", 3),
        ("2026-12-01", "3530.50", 3),
    ]
    assert body["grand_total"] == "7061.00" and body["project_count"] == 3
    assert body["fiscal_years"] == [
        {"fiscal_year": "FY27", "total": "7061.00", "months_with_data": 3}
    ]
    assert sum(Decimal(m["total"]) for m in body["months"]) == Decimal(body["grand_total"])


async def test_totals_respect_filters(api: AsyncClient) -> None:
    body = (await api.get("/v1/revenue/monthly-totals?customer=Acme")).json()
    assert [m["total"] for m in body["months"]] == ["1000.00", "2500.50", "3500.50"]
    assert body["project_count"] == 2
    empty = (await api.get("/v1/revenue/monthly-totals?customer=Nobody")).json()
    assert empty["grand_total"] == "0.00" and len(empty["months"]) == 3


async def test_months_endpoint_flags_closed_months_from_the_as_of_date(
    api: AsyncClient,
) -> None:
    body = (await api.get("/v1/months")).json()
    assert body["as_of"] == "2026-10-01"
    assert [(m["label"], m["fiscal_year"], m["is_closed"]) for m in body["months"]] == [
        ("Oct 2026", "FY27", False), ("Nov 2026", "FY27", False), ("Dec 2026", "FY27", False),
    ]  # fmt: skip


async def test_import_results_and_reconciliation(api: AsyncClient) -> None:
    latest = (await api.get("/v1/imports/latest")).json()
    assert latest["status"] == "completed" and latest["reconciled"] is True
    assert (latest["rows_read"], latest["projects_created"], latest["rows_rejected"]) == (3, 3, 0)
    assert [i["code"] for i in latest["issues"]] == ["missing_client_partner"]
    assert all(r["matches"] and r["variance"] == "0.00" for r in latest["reconciliation"])
    assert latest["reconciliation"][0]["source_total"] == "1010.00"

    assert (await api.get(f"/v1/imports/{latest['id']}")).json()["id"] == latest["id"]
    assert [r["id"] for r in (await api.get("/v1/imports")).json()] == [latest["id"]]
    assert (await api.get("/v1/imports/9999")).status_code == 404


async def test_no_import_yet_is_a_clean_404(session: AsyncSession, as_user: SignedIn) -> None:
    await grant(session, email="lead@corp.test", roles=["Finance"], code=None, name="Lee Lead")
    http, _ = await as_user("lead@corp.test")
    assert (await http.get("/v1/imports/latest")).status_code == 404
    assert (await http.get("/v1/projects")).json()["items"] == []


async def test_api_is_read_only_apart_from_the_guarded_month_edit(api: AsyncClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        for path in ("/v1/projects", "/v1/projects/1", "/v1/projects/1/revenue", "/v1/imports"):
            response = await getattr(api, method)(path)
            assert response.status_code == 405, (method, path)


@pytest.mark.parametrize("environment", ["development", "staging", "production"])
async def test_financial_endpoints_require_sign_in_in_every_environment(
    api: AsyncClient, client: AsyncClient, environment: str
) -> None:
    app.dependency_overrides[get_settings] = lambda: make_settings(app_env=environment)  # pyright: ignore[reportArgumentType]
    try:
        for path in (
            "/v1/projects",
            "/v1/months",
            "/v1/revenue/monthly-totals",
            "/v1/imports/latest",
        ):
            assert (await client.get(path)).status_code == 401, path
            assert (await api.get(path)).status_code in (200, 404), path  # signed in: reachable
        assert (await client.get("/health")).status_code == 200  # Milestone 0 stays reachable
    finally:
        app.dependency_overrides[get_settings] = lambda: make_settings()
