"""Monthly editing: permissions, validation, concurrency, audit and import-baseline integrity."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.migration.loader import import_workbook
from app.migration.workbook import parse_workbook
from app.models import (
    ImportIssue,
    Project,
    ProjectMonthlyRevenue,
    RevenueRevision,
    User,
)
from app.provisioning import grant
from tests.conftest import SignedIn
from tests.test_import import run_import
from tests.workbook_builder import MONTHS, WorkbookBuilder

OCT, NOV, DEC = (m.isoformat() for m in MONTHS)

PEOPLE: list[tuple[str | None, str, list[str], str | None]] = [
    ("AC", "avery@corp.test", ["Client Partner"], None),
    ("JM", "jordan@corp.test", ["Client Partner"], None),
    ("Shah", "riley@corp.test", ["Seller / Account Executive"], None),
    ("AF", "alex@corp.test", ["Commercial Owner"], None),
    ("PN", "priya@corp.test", ["Client Partner", "Commercial Owner"], None),
    ("FO", "finowner@corp.test", ["Finance"], None),
    (None, "cro@corp.test", ["CRO / Sales Leadership"], "Casey CRO"),
    (None, "admin@corp.test", ["Administrator"], "Ada Admin"),
    (None, "fin@corp.test", ["Finance"], "Fin Ance"),
    (None, "res@corp.test", ["Delivery / Resourcing"], "Re Source"),
    (None, "sam@corp.test", ["Client Partner"], "Sam NotOwner"),
    (None, "seller@corp.test", ["Seller / Account Executive"], "Sel Ler"),
]


@dataclass
class World:
    ids: dict[str, int]  # workbook Row ID -> project id
    builder: WorkbookBuilder


def put(
    http: AsyncClient,
    headers: dict[str, str],
    project_id: int,
    month: str,
    body: dict[str, Any],
) -> Any:
    return http.put(f"/v1/projects/{project_id}/revenue/{month}", json=body, headers=headers)


def edit(amount: object, expected: object, **extra: object) -> dict[str, object]:
    return {"amount": amount, "expected_amount": expected, **extra}


@pytest_asyncio.fixture
async def world(session: AsyncSession, builder: WorkbookBuilder) -> World:
    builder.users.append(("FO", "Fin Owner", "Finance", "fo@example.com", "Canada", None))
    builder.add("R1", Customer="Acme", Project="Portal", amounts=[1000.0, 2000.0, 3000.0])
    builder.add(
        "R2", Customer="Acme", Project="Mobile", AE="Shah", **{"Client Partner": "JM"},
        **{"Commercial Owner": "AF"}, amounts=[None, 500.5, 500.5],
    )  # fmt: skip
    builder.add("R3", Customer="Globex", Project="Core", amounts=[10.0, 20.0, 30.0])
    builder.add("R4", Customer="Initech", Project="Ledger", **{"Client Partner": "FO"})
    await run_import(session, builder)
    for code, email, roles, name in PEOPLE:
        await grant(session, email=email, roles=roles, code=code, name=name)
    rows = await session.execute(select(Project.source_row_id, Project.id))
    return World(ids={row_id: pid for row_id, pid in rows}, builder=builder)


async def amount_of(session: AsyncSession, project_id: int, month: str) -> Decimal | None:
    session.expire_all()
    return await session.scalar(
        select(ProjectMonthlyRevenue.amount).where(
            ProjectMonthlyRevenue.project_id == project_id,
            ProjectMonthlyRevenue.month == MONTHS[[OCT, NOV, DEC].index(month)],
        )
    )


async def edit_events(session: AsyncSession) -> list[RevenueRevision]:
    session.expire_all()
    rows = await session.scalars(
        select(RevenueRevision)
        .where(RevenueRevision.field == "revenue")
        .order_by(RevenueRevision.id)
    )
    return list(rows)


# ---- who can edit what -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "row", "allowed"),
    [
        ("avery@corp.test", "R1", True),  # assigned Client Partner
        ("jordan@corp.test", "R1", False),  # Client Partner, not this project's
        ("jordan@corp.test", "R2", True),
        ("riley@corp.test", "R2", True),  # assigned Account Executive
        ("riley@corp.test", "R1", False),
        ("alex@corp.test", "R2", True),  # assigned Commercial Owner
        ("alex@corp.test", "R1", False),
        ("priya@corp.test", "R1", False),  # dual role, but owns neither
        ("sam@corp.test", "R3", False),
        ("seller@corp.test", "R2", False),  # Seller role, not the assigned AE
        ("cro@corp.test", "R1", True),  # Sales leadership: any project
        ("cro@corp.test", "R4", True),
        ("admin@corp.test", "R3", True),  # Administrator: any project
        ("fin@corp.test", "R1", False),  # Finance is read-only
        ("res@corp.test", "R1", False),  # Delivery / Resourcing is read-only
        ("finowner@corp.test", "R4", False),  # named as owner but holds no editing role
    ],
)
async def test_permission_matrix_is_enforced_by_the_api(
    world: World, as_user: SignedIn, session: AsyncSession, email: str, row: str, allowed: bool
) -> None:
    http, headers = await as_user(email)
    pid = world.ids[row]
    before = await amount_of(session, pid, NOV)
    response = await put(http, headers, pid, NOV, edit("4321.5", str(before) if before else None))

    if allowed:
        assert response.status_code == 200, response.text
        assert await amount_of(session, pid, NOV) == Decimal("4321.50")
        assert len(await edit_events(session)) == 1
    else:
        assert response.status_code == 403, response.text
        assert await amount_of(session, pid, NOV) == before  # untouched
        assert await edit_events(session) == []  # and no audit event


async def test_refusal_names_the_responsible_owner(world: World, as_user: SignedIn) -> None:
    http, headers = await as_user("sam@corp.test")
    response = await put(http, headers, world.ids["R2"], NOV, edit(1, "500.50"))

    detail = response.json()["detail"]
    assert response.status_code == 403 and detail["code"] == "not_project_owner"
    assert "Jordan Malik (Client Partner)" in detail["message"]
    assert "Riley Shah (Account Executive)" in detail["message"]
    assert {"name": "Alex Fraser", "role": "Commercial Owner"} in detail["owners"]


async def test_read_only_roles_are_told_and_shown_the_owner(
    world: World, as_user: SignedIn
) -> None:
    http, headers = await as_user("fin@corp.test")
    detail = (await put(http, headers, world.ids["R1"], NOV, edit(1, "2000.00"))).json()["detail"]
    assert detail["code"] == "read_only_role"
    assert "Avery Chen" in detail["message"] and "read-only" in detail["message"]


async def test_permission_is_checked_before_the_body_is_validated(
    world: World, as_user: SignedIn
) -> None:
    http, headers = await as_user("sam@corp.test")
    response = await put(http, headers, world.ids["R1"], NOV, edit("=1+1", "oops"))
    assert response.status_code == 403  # not a 422 that would reveal how validation works


async def test_project_list_and_detail_report_what_each_person_may_edit(
    world: World, as_user: SignedIn
) -> None:
    async def editable(email: str) -> set[str]:
        http, _ = await as_user(email)
        items = (await http.get("/v1/projects?page_size=500")).json()["items"]
        return {p["source_row_id"] for p in items if p["can_edit"]}

    assert await editable("avery@corp.test") == {"R1", "R3"}
    assert await editable("jordan@corp.test") == {"R2"}
    assert await editable("cro@corp.test") == {"R1", "R2", "R3", "R4"}
    assert await editable("fin@corp.test") == set()

    http, _ = await as_user("avery@corp.test")
    mine = (await http.get(f"/v1/projects/{world.ids['R1']}")).json()
    other = (await http.get(f"/v1/projects/{world.ids['R2']}")).json()
    assert (mine["can_edit"], mine["can_view_history"]) == (True, True)
    assert (other["can_edit"], other["can_view_history"]) == (False, False)  # may view, not edit


async def test_a_user_cannot_edit_by_changing_the_project_id(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    for row in ("R2", "R4"):
        response = await put(http, headers, world.ids[row], NOV, edit(1, None))
        assert response.status_code == 403
    assert (await put(http, headers, 99999, NOV, edit(1, None))).status_code == 404


# ---- authentication and CSRF on the mutation ---------------------------------------------


async def test_edit_requires_sign_in_and_a_csrf_token(
    world: World, client: AsyncClient, as_user: SignedIn, session: AsyncSession
) -> None:
    pid = world.ids["R1"]
    body = edit(1, "2000.00")
    assert (await put(client, {}, pid, NOV, body)).status_code == 401

    http, headers = await as_user("avery@corp.test")
    missing = await put(http, {}, pid, NOV, body)
    wrong = await put(http, {"X-CSRF-Token": "forged"}, pid, NOV, body)
    assert missing.status_code == wrong.status_code == 403
    assert missing.json()["detail"]["code"] == "csrf_failed"
    assert await amount_of(session, pid, NOV) == Decimal("2000.00")
    assert (await put(http, headers, pid, NOV, body)).status_code == 200


# ---- validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "-5", -5, -0.01, "abc", "=90000-55000", "+5", "1e5", "1E3", "", "  ", "12.345", 12.345,
        "1,000", "$100", "NaN", "Infinity", True, False, [1], {"a": 1}, None,
        "9" * 13, "١٢٣", "1_000", "0x10",
    ],
)  # fmt: skip
async def test_invalid_amounts_are_rejected_and_nothing_changes(
    world: World, as_user: SignedIn, session: AsyncSession, bad: object
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    response = await put(http, headers, pid, NOV, edit(bad, "2000.00"))

    assert response.status_code == 422, (bad, response.text)
    assert response.json()["detail"]["code"] in {"invalid_amount", "negative_amount"}
    assert await amount_of(session, pid, NOV) == Decimal("2000.00")
    assert await edit_events(session) == []


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("0", "0.00"), (0, "0.00"), ("1234.5", "1234.50"), (100.5, "100.50"), (7, "7.00"),
     (" 42 ", "42.00"), ("999999999999.99", "999999999999.99"), (0.1, "0.10")],
)  # fmt: skip
async def test_valid_amounts_are_stored_exactly(
    world: World, as_user: SignedIn, session: AsyncSession, sent: object, stored: str
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    response = await put(http, headers, pid, NOV, edit(sent, "2000.00"))

    assert response.status_code == 200, response.text
    assert response.json()["amount"] == stored
    assert await amount_of(session, pid, NOV) == Decimal(stored)


@pytest.mark.parametrize(
    ("month", "code"),
    [("2026-11-15", "invalid_month"), ("2026-11-02", "invalid_month"),
     ("2030-01-01", "month_out_of_range"), ("2020-01-01", "month_out_of_range")],
)  # fmt: skip
async def test_invalid_months_are_rejected(
    world: World, as_user: SignedIn, session: AsyncSession, month: str, code: str
) -> None:
    http, headers = await as_user("avery@corp.test")
    response = await put(http, headers, world.ids["R1"], month, edit(5, None))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert await edit_events(session) == []


@pytest.mark.parametrize("month", ["not-a-date", "2026-13-01", "2026-11"])
async def test_malformed_months_are_rejected(world: World, as_user: SignedIn, month: str) -> None:
    http, headers = await as_user("avery@corp.test")
    assert (await put(http, headers, world.ids["R1"], month, edit(5, None))).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"expected_amount": "1.00"},
        {"amount": 5},
        {"amount": 5, "expected_amount": None, "x": 1},
        {},
    ],
)
async def test_missing_or_unknown_fields_are_rejected(
    world: World, as_user: SignedIn, body: dict[str, object]
) -> None:
    http, headers = await as_user("avery@corp.test")
    assert (await put(http, headers, world.ids["R1"], NOV, body)).status_code == 422


async def test_reason_is_optional_trimmed_and_length_limited(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    assert (
        await put(http, headers, pid, NOV, edit(1, "2000.00", reason="x" * 501))
    ).status_code == 422
    assert (await put(http, headers, pid, NOV, edit(1, "2000.00", reason=5))).status_code == 422
    ok = await put(http, headers, pid, NOV, edit(1, "2000.00", reason="  Scope trimmed  "))
    assert ok.status_code == 200
    assert (await edit_events(session))[0].note == "Scope trimmed"


# ---- the edit itself ---------------------------------------------------------------------


async def test_edit_changes_only_the_target_month_and_records_a_complete_audit_event(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    avery = await session.scalar(select(User).where(User.email == "avery@corp.test"))
    assert avery is not None
    avery_id = avery.id
    pid = world.ids["R1"]
    response = await put(
        http, headers, pid, NOV, edit("2500", "2000.00", reason="Client added scope")
    )

    body = response.json()
    assert response.status_code == 200
    assert (body["amount"], body["previous_amount"], body["changed"]) == (
        "2500.00",
        "2000.00",
        True,
    )
    assert body["project_total"] == "6500.00"  # 1000 + 2500 + 3000
    assert (await amount_of(session, pid, OCT), await amount_of(session, pid, DEC)) == (
        Decimal("1000.00"),
        Decimal("3000.00"),
    )  # unrelated months untouched

    (event,) = await edit_events(session)
    assert (event.project_id, event.month, event.change_type) == (pid, MONTHS[1], "update")
    assert (event.old_value, event.new_value) == ("2000.00", "2500.00")
    assert (event.actor_user_id, event.actor_label, event.note) == (
        avery_id, "Avery Chen", "Client added scope",
    )  # fmt: skip
    assert event.created_at is not None and event.import_run_id is None


async def test_editing_never_changes_the_import_baseline(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    await put(http, headers, pid, NOV, edit(9999, "2000.00"))

    session.expire_all()
    row = await session.scalar(
        select(ProjectMonthlyRevenue).where(
            ProjectMonthlyRevenue.project_id == pid, ProjectMonthlyRevenue.month == MONTHS[1]
        )
    )
    assert row is not None and (row.amount, row.baseline_amount) == (
        Decimal("9999.00"), Decimal("2000.00"),
    )  # fmt: skip
    detail = (await http.get(f"/v1/projects/{pid}")).json()
    nov = next(m for m in detail["monthly_revenue"] if m["month"] == NOV)
    assert (nov["amount"], nov["baseline_amount"], nov["edited"]) == ("9999.00", "2000.00", True)
    oct_ = next(m for m in detail["monthly_revenue"] if m["month"] == OCT)
    assert oct_["edited"] is False

    current = (await http.get("/v1/revenue/monthly-totals")).json()
    baseline = (await http.get("/v1/revenue/monthly-totals?basis=baseline")).json()
    assert (current["basis"], baseline["basis"]) == ("current", "baseline")
    assert current["months"][1]["total"] == str(Decimal(baseline["months"][1]["total"]) + 7999)
    assert baseline["months"][0]["total"] == current["months"][0]["total"]  # other months match


async def test_a_blank_month_can_be_filled_and_is_marked_as_added_in_the_app(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("jordan@corp.test")
    pid = world.ids["R2"]  # October is blank in the workbook
    response = await put(http, headers, pid, OCT, edit("750.25", None))

    assert response.status_code == 200 and response.json()["previous_amount"] is None
    (event,) = await edit_events(session)
    assert (event.change_type, event.old_value, event.new_value) == ("create", None, "750.25")
    session.expire_all()
    row = await session.scalar(
        select(ProjectMonthlyRevenue).where(
            ProjectMonthlyRevenue.project_id == pid, ProjectMonthlyRevenue.month == MONTHS[0]
        )
    )
    assert row is not None and row.baseline_amount is None


async def test_saving_an_unchanged_value_is_a_no_op_without_an_audit_event(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    response = await put(http, headers, world.ids["R1"], NOV, edit("2000", "2000.00"))
    assert response.status_code == 200 and response.json()["changed"] is False
    assert response.json()["revision_id"] is None
    assert await edit_events(session) == []


# ---- concurrency -------------------------------------------------------------------------


async def test_stale_edits_get_a_conflict_with_the_latest_value(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    avery, avery_headers = await as_user("avery@corp.test")
    cro, cro_headers = await as_user("cro@corp.test")
    pid = world.ids["R1"]

    assert (await put(cro, cro_headers, pid, NOV, edit(3000, "2000.00"))).status_code == 200
    stale = await put(avery, avery_headers, pid, NOV, edit(2500, "2000.00"))

    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "conflict" and detail["current_amount"] == "3000.00"
    assert await amount_of(session, pid, NOV) == Decimal("3000.00")  # nothing silently overwritten
    assert len(await edit_events(session)) == 1

    retry = await put(avery, avery_headers, pid, NOV, edit(2500, detail["current_amount"]))
    assert retry.status_code == 200
    assert [(e.old_value, e.new_value) for e in await edit_events(session)] == [
        ("2000.00", "3000.00"), ("3000.00", "2500.00"),
    ]  # fmt: skip


async def test_filling_a_blank_month_conflicts_if_someone_else_filled_it_first(
    world: World, as_user: SignedIn
) -> None:
    jordan, jh = await as_user("jordan@corp.test")
    cro, ch = await as_user("cro@corp.test")
    pid = world.ids["R2"]
    assert (await put(cro, ch, pid, OCT, edit(100, None))).status_code == 200
    late = await put(jordan, jh, pid, OCT, edit(200, None))
    assert late.status_code == 409 and late.json()["detail"]["current_amount"] == "100.00"


async def test_simultaneous_edits_cannot_both_win(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    avery, ah = await as_user("avery@corp.test")
    cro, ch = await as_user("cro@corp.test")
    pid = world.ids["R1"]
    results: list[Response] = await asyncio.gather(
        put(avery, ah, pid, NOV, edit(111, "2000.00")), put(cro, ch, pid, NOV, edit(222, "2000.00"))
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r for r in results if r.status_code == 200).json()["amount"]
    assert str(await amount_of(session, pid, NOV)) == winner
    assert len(await edit_events(session)) == 1


# ---- the value and its audit event succeed or fail together ------------------------------


async def test_a_failing_audit_write_rolls_back_the_value(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    long_name = "N" * 150  # does not fit the audit event's actor_label column
    await grant(session, email="long@corp.test", roles=["Administrator"], code=None, name=long_name)
    http, headers = await as_user("long@corp.test")
    pid = world.ids["R1"]

    response = await put(http, headers, pid, NOV, edit(777, "2000.00"))

    assert response.status_code == 500
    assert await amount_of(session, pid, NOV) == Decimal("2000.00")  # not 777: rolled back
    assert await edit_events(session) == []


async def test_the_largest_accepted_amount_fits_the_money_column(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    """Validation allows 12 integer digits, exactly what NUMERIC(14,2) holds, so an accepted
    value can never fail at the database after the audit event was prepared."""
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    response = await put(http, headers, pid, NOV, edit("999999999999.99", "2000.00", reason="ok"))
    assert response.status_code == 200
    assert await amount_of(session, pid, NOV) == Decimal("999999999999.99")
    assert len(await edit_events(session)) == 1


# ---- history -----------------------------------------------------------------------------


async def test_history_lists_who_when_what_and_why_newest_first(
    world: World, as_user: SignedIn
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    await put(http, headers, pid, NOV, edit(2100, "2000.00", reason="First"))
    await put(http, headers, pid, DEC, edit(3300, "3000.00"))

    body = (await http.get(f"/v1/projects/{pid}/history")).json()
    items = body["items"]
    assert body["total_items"] == 3  # two edits + the import's "created" event
    assert [(i["field"], i["month"], i["old_value"], i["new_value"]) for i in items[:2]] == [
        ("revenue", DEC, "3000.00", "3300.00"),
        ("revenue", NOV, "2000.00", "2100.00"),
    ]
    assert items[0]["actor_name"] == "Avery Chen" and items[0]["reason"] is None
    assert items[1]["reason"] == "First" and items[1]["created_at"]
    assert items[2]["from_import"] is True and items[2]["field"] == "project"
    assert items[0]["from_import"] is False


@pytest.mark.parametrize(
    ("email", "visible"),
    [("avery@corp.test", True), ("cro@corp.test", True), ("admin@corp.test", True),
     ("fin@corp.test", True), ("jordan@corp.test", False), ("res@corp.test", False),
     ("riley@corp.test", False)],
)  # fmt: skip
async def test_history_is_limited_to_people_entitled_to_see_it(
    world: World, as_user: SignedIn, email: str, visible: bool
) -> None:
    http, _ = await as_user(email)
    response = await http.get(f"/v1/projects/{world.ids['R1']}/history")
    assert response.status_code == (200 if visible else 403)
    if not visible:
        assert response.json()["detail"]["code"] == "history_forbidden"


async def test_history_requires_sign_in_and_a_real_project(
    world: World, client: AsyncClient, as_user: SignedIn
) -> None:
    assert (await client.get(f"/v1/projects/{world.ids['R1']}/history")).status_code == 401
    http, _ = await as_user("cro@corp.test")
    assert (await http.get("/v1/projects/99999/history")).status_code == 404


async def test_audit_events_cannot_be_changed_or_deleted(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    await put(http, headers, pid, NOV, edit(5, "2000.00"))

    for method in ("post", "put", "patch", "delete"):
        response = await getattr(http, method)(f"/v1/projects/{pid}/history", headers=headers)
        assert response.status_code == 405, method

    with pytest.raises(DBAPIError, match="append-only"):  # even direct SQL is refused
        await session.execute(text("UPDATE revenue_revisions SET new_value = '1'"))
    await session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("DELETE FROM revenue_revisions"))
    await session.rollback()
    assert len(await edit_events(session)) == 1


# ---- imported data integrity -------------------------------------------------------------


async def test_reimporting_after_edits_keeps_edits_and_still_reconciles(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    await put(http, headers, pid, NOV, edit(8888, "2000.00"))
    await put(http, headers, world.ids["R3"], OCT, edit(0, "10.00"))
    before = await session.scalar(select(func.sum(ProjectMonthlyRevenue.amount)))

    report = await import_workbook(session, parse_workbook(world.builder.build()))

    assert report.reconciled and all(r.matches for r in report.reconciliation)  # baseline == source
    assert (report.projects_created, report.projects_updated) == (0, 0)
    session.expire_all()
    assert await amount_of(session, pid, NOV) == Decimal("8888.00")  # edit survived
    assert await session.scalar(select(func.sum(ProjectMonthlyRevenue.amount))) == before
    assert not [i for i in report.issues if i.code == "edited_in_app_kept"]


async def test_reimport_with_changed_workbook_updates_untouched_months_but_keeps_edits(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    pid = world.ids["R1"]
    await put(http, headers, pid, NOV, edit(8888, "2000.00"))  # edited in the app

    world.builder.rows[0]["amounts"] = [1100.0, 2200.0, None]  # workbook now differs everywhere
    report = await import_workbook(session, parse_workbook(world.builder.build()))
    kept = [
        (i.source_row_id, i.field, i.message)
        for i in report.issues
        if i.code == "edited_in_app_kept"
    ]
    assert report.reconciled  # the baseline always matches the workbook

    assert await amount_of(session, pid, OCT) == Decimal("1100.00")  # untouched: followed source
    assert await amount_of(session, pid, NOV) == Decimal("8888.00")  # edited month kept
    assert await amount_of(session, pid, DEC) is None  # untouched, blank in source: removed
    assert [(row_id, field) for row_id, field, _ in kept] == [("R1", "2026-11")]
    assert "8888" in kept[0][2] and "2200" in kept[0][2]
    stored = await session.scalar(
        select(func.count())
        .select_from(ImportIssue)
        .where(ImportIssue.code == "edited_in_app_kept")
    )
    assert stored == 1


async def test_reimport_never_resets_a_provisioned_persons_identity(
    world: World, session: AsyncSession
) -> None:
    await import_workbook(session, parse_workbook(world.builder.build()))
    session.expire_all()
    avery = await session.scalar(select(User).where(User.code == "AC"))
    assert avery is not None and avery.email == "avery@corp.test" and avery.is_provisioned
    await session.refresh(avery, ["roles"])
    assert [r.name for r in avery.roles] == ["Client Partner"]


async def test_edits_do_not_alter_the_import_run_records(
    world: World, as_user: SignedIn, session: AsyncSession
) -> None:
    http, headers = await as_user("avery@corp.test")
    await put(http, headers, world.ids["R1"], NOV, edit(1, "2000.00"))
    latest = (await http.get("/v1/imports/latest")).json()
    assert latest["reconciled"] is True and latest["rows_rejected"] == 0
    assert all(r["matches"] for r in latest["reconciliation"])
