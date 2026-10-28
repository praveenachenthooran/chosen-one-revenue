"""QA API: permissions, review workflow, routing, investigations (deterministic and Claude), health."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.qa import get_ai_client_factory, get_session_factory
from app.config import get_settings
from app.main import app
from app.models import Investigation, QaFinding, QaFindingEvent, QaRun, User
from app.pipeline.sync import run_sync
from app.qa import detective
from app.qa.engine import run_qa
from app.qa.tools import ToolContext, call_tool
from tests.conftest import SignedIn
from tests.helpers import make_settings
from tests.pipeline_helpers import sdeal, snapshot
from tests.qa_seed import NOW, Seed, create_seed, deals
from tests.workbook_builder import WorkbookBuilder

AVERY, RILEY, CRO, ADMIN, FIN, RES, SAM = (
    "avery@corp.test", "riley@corp.test", "cro@corp.test", "admin@corp.test",
    "fin@corp.test", "res@corp.test", "sam@corp.test",
)  # fmt: skip
EVERYONE = [AVERY, RILEY, CRO, ADMIN, FIN, RES, SAM]


@pytest_asyncio.fixture
async def seed(session: AsyncSession, builder: WorkbookBuilder) -> Seed:
    return await create_seed(session, builder)


@pytest_asyncio.fixture
async def qa(seed: Seed, client: AsyncClient, engine: AsyncEngine) -> Seed:
    """The seeded data with one QA run done, and the background tasks pointed at the test database."""
    result = await run_qa(seed.session, make_settings(), trigger="scheduled", now=NOW)
    assert result.new == 20
    await seed.session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: factory
    return seed


async def user_with_roles(session: AsyncSession, email: str) -> User:
    user = await session.scalar(
        select(User).where(User.email == email).options(selectinload(User.roles))
    )
    assert user is not None
    return user


async def get(http: AsyncClient, path: str, **params: Any) -> Any:
    response = await http.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def finding_for(
    http: AsyncClient, rule: str, key: str | None = None, **params: Any
) -> dict[str, Any]:
    items = (await get(http, "/v1/qa/findings", rule=rule, **params))["items"]
    if key is None:
        return items[0]
    return next(i for i in items if key in i["title"] or key in i["summary"])


def use_ai(client_factory: Any = None, **overrides: Any) -> None:
    settings = make_settings(
        anthropic_api_key="sk-test-not-real", ai_data_use_approved=True, **overrides
    )
    app.dependency_overrides[get_settings] = lambda: settings
    if client_factory is not None:
        app.dependency_overrides[get_ai_client_factory] = lambda: client_factory


# ---- access ------------------------------------------------------------------------------------

READS = [
    "/v1/qa/findings",
    "/v1/qa/findings/1",
    "/v1/qa/runs",
    "/v1/qa/health",
    "/v1/qa/investigations/1",
]


async def test_every_qa_endpoint_requires_sign_in(client: AsyncClient) -> None:
    for path in READS:
        assert (await client.get(path)).status_code == 401, path
    for path in (
        "/v1/qa/runs",
        "/v1/qa/findings/1/accept",
        "/v1/qa/findings/1/dismiss",
        "/v1/qa/findings/1/investigate",
    ):
        assert (await client.post(path, json={})).status_code == 401, path


async def test_changes_need_the_csrf_token(qa: Seed, as_user: SignedIn) -> None:
    http, _ = await as_user(ADMIN)
    for path in (
        "/v1/qa/runs",
        "/v1/qa/findings/1/accept",
        "/v1/qa/findings/1/dismiss",
        "/v1/qa/findings/1/investigate",
    ):
        response = await http.post(path, json={"reason": "because"})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "csrf_failed", (
            path
        )


@pytest.mark.parametrize("email", EVERYONE)
async def test_every_signed_in_role_can_read_findings_and_health(
    qa: Seed, as_user: SignedIn, email: str
) -> None:
    http, _ = await as_user(email)
    assert (await get(http, "/v1/qa/findings"))["total_items"] == 20
    assert (await get(http, "/v1/qa/health"))["open_total"] == 20
    assert (await get(http, "/v1/qa/findings/1"))["id"] == 1
    assert len(await get(http, "/v1/qa/runs")) == 1


async def test_unknown_finding_and_investigation_are_404(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    assert (await http.get("/v1/qa/findings/999")).status_code == 404
    assert (await http.get("/v1/qa/investigations/999")).status_code == 404
    assert (
        await http.post("/v1/qa/findings/999/accept", json={"reason": "x y z"}, headers=headers)
    ).status_code == 404


# ---- listing and filters ---------------------------------------------------------------------------


async def test_findings_are_ordered_errors_first_then_by_revenue_at_stake(
    qa: Seed, as_user: SignedIn
) -> None:
    http, _ = await as_user(CRO)
    items = (await get(http, "/v1/qa/findings"))["items"]
    ranks = [{"error": 0, "warning": 1, "info": 2}[i["severity"]] for i in items]
    assert ranks == sorted(ranks)
    first_warning = ranks.index(1)
    warnings = [Decimal(i["financial_impact"]) for i in items[first_warning:]]
    assert warnings == sorted(warnings, reverse=True)


async def test_filters_by_rule_severity_owner_and_project(qa: Seed, as_user: SignedIn) -> None:
    http, _ = await as_user(AVERY)
    assert (await get(http, "/v1/qa/findings", rule="duplicate_project"))["total_items"] == 4
    assert (await get(http, "/v1/qa/findings", rule="stale_row"))["total_items"] == 3
    errors = (await get(http, "/v1/qa/findings", severity="error"))["items"]
    assert errors and all(i["severity"] == "error" for i in errors)
    unassigned = (await get(http, "/v1/qa/findings", unassigned="true"))["items"]
    assert {i["rule_code"] for i in unassigned} == {"missing_required"} and len(unassigned) == 2
    mine = (await get(http, "/v1/qa/findings", mine="true"))["items"]
    assert mine and all(i["routed_to_name"] == "Avery Chen" for i in mine)
    dup = await finding_for(http, "duplicate_project", "R3001")
    both = (await get(http, "/v1/qa/findings", project_id=dup["related_project_ids"][0]))["items"]
    assert dup["id"] in {i["id"] for i in both}  # a duplicate is found from either row
    assert (await get(http, "/v1/qa/findings", status="all"))["total_items"] == 20
    assert (await get(http, "/v1/qa/findings", status="dismissed"))["total_items"] == 0


async def test_detail_shows_rule_evidence_affected_projects_deal_and_audit_trail(
    qa: Seed, as_user: SignedIn
) -> None:
    http, _ = await as_user(AVERY)
    summary = await finding_for(http, "duplicate_project", "R3001")
    detail = await get(http, f"/v1/qa/findings/{summary['id']}")
    assert detail["rule_label"] == "Possible duplicate project" and detail["suggested_fix"]
    assert {p["row_id"] for p in detail["projects"]} == {"R3001", "R3002"}
    assert detail["evidence"]["rows"][0]["customer"] == "Lakemoor Pharma"
    assert [e["event_type"] for e in detail["events"]] == ["created"]
    assert detail["can_review"] is True and detail["investigations"] == []

    deal = await finding_for(http, "closed_won_missing", "901")
    deal_detail = await get(http, f"/v1/qa/findings/{deal['id']}")
    assert (
        deal_detail["deal"]["hubspot_deal_id"] == "901"
        and deal_detail["opportunity_id"] == deal_detail["deal"]["id"]
    )


# ---- running the checks ----------------------------------------------------------------------------


@pytest.mark.parametrize("email", [CRO, FIN, ADMIN])
async def test_leadership_finance_and_admin_can_run_the_checks(
    qa: Seed, as_user: SignedIn, email: str
) -> None:
    http, headers = await as_user(email)
    response = await http.post("/v1/qa/runs", headers=headers)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "completed" and run["trigger"] == "manual"
    assert (
        run["findings_new"] == 0 and run["findings_seen"] == 20 and run["findings_open"] == 20
    )  # idempotent


@pytest.mark.parametrize("email", [AVERY, RILEY, RES, SAM])
async def test_other_roles_cannot_run_the_checks(qa: Seed, as_user: SignedIn, email: str) -> None:
    http, headers = await as_user(email)
    response = await http.post("/v1/qa/runs", headers=headers)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "forbidden_role"
    assert await qa.session.scalar(select(func.count()).select_from(QaRun)) == 1


async def test_a_run_while_another_is_running_is_refused(
    qa: Seed, as_user: SignedIn, engine: AsyncEngine
) -> None:
    http, headers = await as_user(ADMIN)
    other = async_sessionmaker(engine)()
    async with other:
        await other.execute(text("SELECT pg_advisory_xact_lock(7301003)"))
        response = await http.post("/v1/qa/runs", headers=headers)
        await other.rollback()
    assert response.status_code == 409 and response.json()["detail"]["code"] == "qa_busy"


# ---- human review --------------------------------------------------------------------------------


async def test_the_routed_owner_can_accept_with_a_reason_and_it_is_audited(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(AVERY)
    target = await finding_for(http, "stale_row", "R3010")
    assert target["can_review"] is True and target["routed_to_name"] == "Avery Chen"
    before = (
        await qa.session.execute(text("SELECT sum(amount) FROM project_monthly_revenue"))
    ).scalar_one()

    response = await http.post(
        f"/v1/qa/findings/{target['id']}/accept",
        json={"reason": "Will review this week"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["review_reason"], body["reviewed_by_name"]) == (
        "accepted",
        "Will review this week",
        "Avery Chen",
    )
    assert body["reviewed_at"] and body["can_review"] is False

    detail = await get(http, f"/v1/qa/findings/{target['id']}")
    last = detail["events"][-1]
    assert (last["event_type"], last["actor_label"]) == ("accepted", "Avery Chen")
    assert last["details"]["reason"] == "Will review this week"
    after = (
        await qa.session.execute(text("SELECT sum(amount) FROM project_monthly_revenue"))
    ).scalar_one()
    assert after == before  # accepting records a decision; it never changes revenue


async def test_dismiss_requires_a_reason_and_stays_dismissed_on_later_runs(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(AVERY)
    target = await finding_for(http, "stale_row", "R3011")
    for reason in ("", "  ", "ok"):
        response = await http.post(
            f"/v1/qa/findings/{target['id']}/dismiss", json={"reason": reason}, headers=headers
        )
        assert (
            response.status_code == 422 and response.json()["detail"]["code"] == "reason_required"
        )
    assert (
        await http.post(
            f"/v1/qa/findings/{target['id']}/dismiss", json={"extra": 1}, headers=headers
        )
    ).status_code == 422
    ok = await http.post(
        f"/v1/qa/findings/{target['id']}/dismiss",
        json={"reason": "Row is intentionally frozen"},
        headers=headers,
    )
    assert ok.status_code == 200 and ok.json()["status"] == "dismissed"

    admin, admin_headers = await as_user(ADMIN)
    assert (await admin.post("/v1/qa/runs", headers=admin_headers)).status_code == 200
    again = (await get(admin, "/v1/qa/findings", status="all"))["items"]
    assert (
        len(again) == 20
        and next(i for i in again if i["id"] == target["id"])["status"] == "dismissed"
    )
    assert (await get(admin, "/v1/qa/findings"))["total_items"] == 19


async def test_a_non_owner_is_refused_and_told_who_owns_it(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(SAM)
    target = await finding_for(http, "stale_row", "R3012")
    assert target["can_review"] is False
    response = await http.post(
        f"/v1/qa/findings/{target['id']}/accept", json={"reason": "I think so"}, headers=headers
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert (
        detail["code"] == "not_finding_owner"
        and detail["routed_to"] == "Avery Chen"
        and "Avery Chen" in detail["message"]
    )
    assert (await qa.session.get(QaFinding, target["id"])).status == "open"  # type: ignore[union-attr]


@pytest.mark.parametrize("email", [FIN, RES, SAM, RILEY])
async def test_finance_and_read_only_roles_cannot_decide_findings_they_do_not_own(
    qa: Seed, as_user: SignedIn, email: str
) -> None:
    http, headers = await as_user(email)
    target = await finding_for(http, "duplicate_project", "R3001")  # routed to Avery
    response = await http.post(
        f"/v1/qa/findings/{target['id']}/dismiss",
        json={"reason": "not mine to say"},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.parametrize("email", [CRO, ADMIN])
async def test_leadership_and_admin_can_decide_any_finding_including_unassigned(
    qa: Seed, as_user: SignedIn, email: str
) -> None:
    http, headers = await as_user(email)
    unassigned = (await get(http, "/v1/qa/findings", unassigned="true"))["items"][0]
    assert unassigned["can_review"] is True
    response = await http.post(
        f"/v1/qa/findings/{unassigned['id']}/accept",
        json={"reason": "Assigning an owner today"},
        headers=headers,
    )
    assert response.status_code == 200 and response.json()["status"] == "accepted"


async def test_the_deal_owner_can_decide_their_deal_finding(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(RILEY)
    deal = await finding_for(http, "closed_won_missing", "901")
    assert deal["routed_to_name"] == "Riley Shah" and deal["can_review"] is True
    assert (
        await http.post(
            f"/v1/qa/findings/{deal['id']}/accept",
            json={"reason": "Confirming the schedule"},
            headers=headers,
        )
    ).status_code == 200


async def test_a_decided_finding_cannot_be_decided_again(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(CRO)
    target = (await get(http, "/v1/qa/findings"))["items"][0]
    path = f"/v1/qa/findings/{target['id']}"
    assert (
        await http.post(f"{path}/accept", json={"reason": "Real problem"}, headers=headers)
    ).status_code == 200
    again = await http.post(f"{path}/dismiss", json={"reason": "Changed my mind"}, headers=headers)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_open"
    assert (await http.post(f"{path}/investigate", headers=headers)).status_code == 409


# ---- investigations: deterministic -----------------------------------------------------------------


async def investigate(
    http: AsyncClient, headers: dict[str, str], finding_id: int
) -> dict[str, Any]:
    response = await http.post(f"/v1/qa/findings/{finding_id}/investigate", headers=headers)
    assert response.status_code == 202, response.text
    return await get(http, f"/v1/qa/investigations/{response.json()['id']}")


async def test_deterministic_investigation_explains_an_anomaly_and_marks_the_finding_awaiting_review(
    qa: Seed, as_user: SignedIn
) -> None:
    # A Closed Lost deal for the same customer: the Silverpine pattern from the supplied data.
    seeded_deals, links = deals()
    lost = sdeal(
        "930",
        stage="Closed Lost",
        company="Spike Customer",
        name="Spike - Phase 2",
        amount="300000",
    )
    qa.source.current = snapshot([*seeded_deals, lost], links=links)
    assert (await run_sync(qa.session, qa.source, make_settings(), now=NOW)).status == "completed"

    http, headers = await as_user(AVERY)
    target = await finding_for(http, "mom_anomaly", "R3020")
    done = await investigate(http, headers, target["id"])

    assert (
        done["status"] == "completed" and done["mode"] == "deterministic" and done["model"] is None
    )
    assert "API key" in done["fallback_reason"]
    result = done["result"]
    by_status = {c["status"] for c in result["causes"]}
    assert "confirmed" in by_status and "hypothesis" in by_status  # distinguished, never blurred
    for cause in result["causes"]:
        if cause["status"] == "confirmed":
            assert cause["evidence_refs"], cause  # a confirmed cause always cites a record
    statements = " ".join(c["statement"] for c in result["causes"])
    assert "No reason code is recorded" in statements
    lost_cause = next(c for c in result["causes"] if "Closed Lost" in c["statement"])
    assert lost_cause["status"] == "hypothesis" and lost_cause["evidence_refs"][0].startswith(
        "deal:"
    )
    assert result["financial_impact"] == {
        "amount": "21000.00",
        "basis": "the size of the largest month-over-month movement",
        "source": "deterministic",
    }
    assert result["next_steps"] and result["confidence"] in ("high", "medium", "low")
    assert [s["tool"] for s in done["steps"]][:2] == ["get_project_history", "compare_snapshots"]

    detail = await get(http, f"/v1/qa/findings/{target['id']}")
    assert detail["awaiting_review"] is True and detail["investigation_status"] == "completed"
    assert [e["event_type"] for e in detail["events"]] == [
        "created",
        "investigation_started",
        "investigated",
    ]
    assert detail["status"] == "open"  # an investigation never decides anything


async def test_investigation_reads_the_audit_history_for_an_in_app_edit(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(AVERY)
    pid = (
        await qa.session.execute(text("SELECT id FROM projects WHERE source_row_id = 'R3022'"))
    ).scalar_one()
    put = await http.put(
        f"/v1/projects/{pid}/revenue/2027-04-01",
        json={"amount": "30001", "expected_amount": "30000.00"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    admin, admin_headers = await as_user(ADMIN)
    await admin.post("/v1/qa/runs", headers=admin_headers)
    target = await finding_for(admin, "mom_anomaly", "R3022")
    done = await investigate(admin, admin_headers, target["id"])
    statements = [
        c for c in done["result"]["causes"] if "edited in the app by Avery Chen" in c["statement"]
    ]
    assert (
        statements
        and statements[0]["status"] == "confirmed"
        and statements[0]["evidence_refs"][0].startswith("revision:")
    )
    assert done["result"]["confidence"] == "high"


@pytest.mark.parametrize(
    ("rule", "needle", "expected"),
    [
        ("duplicate_project", "R3001", "R3002 was created"),
        ("closed_won_missing", "901", "Draft project"),
        ("closed_won_missing", "903", "No draft project could be created"),
        ("amount_mismatch", "910", "76.00% apart"),
        ("stale_row", "R3010", "days ago"),
        ("missing_required", "R3030", "no owner"),
        ("missing_required", "R3032", "Missing: category"),
    ],
)
async def test_every_rule_has_an_investigation(
    qa: Seed, as_user: SignedIn, rule: str, needle: str, expected: str
) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, rule, needle)
    done = await investigate(http, headers, target["id"])
    assert done["status"] == "completed"
    text_blob = (
        " ".join(c["statement"] for c in done["result"]["causes"]) + done["result"]["summary"]
    )
    assert expected.lower() in text_blob.lower(), text_blob
    assert done["result"]["financial_impact"]["amount"] == target["financial_impact"]


async def test_investigation_permissions(qa: Seed, as_user: SignedIn) -> None:
    sam, sam_headers = await as_user(SAM)
    target = await finding_for(sam, "stale_row", "R3010")
    denied = await sam.post(f"/v1/qa/findings/{target['id']}/investigate", headers=sam_headers)
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "forbidden_role"
    res, res_headers = await as_user(RES)
    assert (
        await res.post(f"/v1/qa/findings/{target['id']}/investigate", headers=res_headers)
    ).status_code == 403
    assert await qa.session.scalar(select(func.count()).select_from(Investigation)) == 0
    for email in (FIN, CRO, AVERY):  # Finance, leadership and the routed owner may
        http, headers = await as_user(email)
        assert (
            await http.post(f"/v1/qa/findings/{target['id']}/investigate", headers=headers)
        ).status_code == 202


async def test_a_second_request_while_one_is_running_reuses_it(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    admin = await qa.session.scalar(select(User).where(User.email == ADMIN))
    assert admin
    running = Investigation(
        finding_id=target["id"],
        status="investigating",
        mode="deterministic",
        requested_by_id=admin.id,
        requested_by_label=admin.name,
        started_at=datetime.now(UTC),
        steps=[],
        transcript=[],
    )
    qa.session.add(running)
    await qa.session.commit()
    response = await http.post(f"/v1/qa/findings/{target['id']}/investigate", headers=headers)
    assert response.status_code == 202 and response.json()["id"] == running.id
    assert await qa.session.scalar(select(func.count()).select_from(Investigation)) == 1


async def test_an_interrupted_investigation_is_shown_as_failed(qa: Seed, as_user: SignedIn) -> None:
    http, _ = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    admin = await qa.session.scalar(select(User).where(User.email == ADMIN))
    assert admin
    old = Investigation(
        finding_id=target["id"],
        status="investigating",
        mode="deterministic",
        requested_by_id=admin.id,
        requested_by_label=admin.name,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        steps=[],
        transcript=[],
    )
    qa.session.add(old)
    await qa.session.commit()
    body = await get(http, f"/v1/qa/investigations/{old.id}")
    assert body["status"] == "failed" and "interrupted" in body["error_message"]


async def test_a_failing_investigation_is_recorded_as_failed(
    qa: Seed, as_user: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: object) -> object:
        raise RuntimeError("tool exploded")

    monkeypatch.setattr(detective, "deterministic_report", boom)
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    done = await investigate(http, headers, target["id"])
    assert (
        done["status"] == "failed"
        and "tool exploded" in done["error_message"]
        and done["result"] is None
    )
    detail = await get(http, f"/v1/qa/findings/{target['id']}")
    assert detail["investigation_status"] == "failed" and detail["awaiting_review"] is False
    assert (
        detail["events"][-1]["event_type"] == "investigation_failed" and detail["status"] == "open"
    )
    monkeypatch.undo()
    assert (await investigate(http, headers, target["id"]))["status"] == "completed"  # retry works


# ---- tools run as the requester, and cannot write --------------------------------------------------


async def test_tools_cannot_see_what_the_requester_cannot_see(qa: Seed) -> None:
    resourcing = await user_with_roles(qa.session, RES)
    admin = await user_with_roles(qa.session, ADMIN)
    pid = (
        await qa.session.execute(text("SELECT id FROM projects WHERE source_row_id = 'R3020'"))
    ).scalar_one()

    denied = await call_tool(
        ToolContext(qa.session, resourcing), "get_project_history", {"project_id": pid}
    )
    assert denied.data["error"] == "not_authorized" and denied.refs == []
    allowed = await call_tool(
        ToolContext(qa.session, admin), "get_project_history", {"project_id": pid}
    )
    assert "error" not in allowed.data and f"project:{pid}" in allowed.refs


async def test_tool_arguments_are_validated_and_unknown_tools_are_harmless(qa: Seed) -> None:
    admin = await user_with_roles(qa.session, ADMIN)
    ctx = ToolContext(qa.session, admin)
    assert (await call_tool(ctx, "get_project_history", {"project_id": "abc"})).data[
        "error"
    ] == "bad_arguments"
    assert (await call_tool(ctx, "get_project_history", {"project_id": 1, "evil": True})).data[
        "error"
    ] == "bad_arguments"
    assert (await call_tool(ctx, "delete_everything", {})).data["error"] == "unknown_tool"
    assert (await call_tool(ctx, "get_project_owners", {"project_id": 99999})).data[
        "error"
    ] == "not_found"
    assert (await call_tool(ctx, "get_hubspot_deals", {})).data["error"] == "bad_request"
    assert [s["outcome"] for s in ctx.steps] == [
        "bad_arguments",
        "bad_arguments",
        "unknown_tool",
        "not_found",
        "bad_request",
    ]


def test_the_agent_has_only_read_tools() -> None:
    from app.qa.tools import TOOLS

    assert set(TOOLS) == {
        "get_project_history",
        "compare_snapshots",
        "get_hubspot_deals",
        "find_duplicates",
        "get_project_owners",
    }
    assert not any(
        any(w in name for w in ("edit", "update", "delete", "accept", "dismiss", "set", "write"))
        for name in TOOLS
    )


async def test_an_investigation_changes_no_revenue_or_findings(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    before_rev = (
        await qa.session.execute(text("SELECT sum(amount), count(*) FROM project_monthly_revenue"))
    ).one()
    before_rev_log = await qa.session.scalar(text("SELECT count(*) FROM revenue_revisions"))
    before = [
        (f.id, f.status, f.financial_impact)
        for f in await qa.session.scalars(select(QaFinding).order_by(QaFinding.id))
    ]
    for item in (await get(http, "/v1/qa/findings"))["items"][:6]:
        await investigate(http, headers, item["id"])
    await qa.session.rollback()
    assert tuple(
        (
            await qa.session.execute(
                text("SELECT sum(amount), count(*) FROM project_monthly_revenue")
            )
        ).one()
    ) == tuple(before_rev)
    assert await qa.session.scalar(text("SELECT count(*) FROM revenue_revisions")) == before_rev_log
    assert [
        (f.id, f.status, f.financial_impact)
        for f in await qa.session.scalars(select(QaFinding).order_by(QaFinding.id))
    ] == before


# ---- investigations: Claude ----------------------------------------------------------------------


class FakeAnthropic:
    """Stands in for the Anthropic client; scripted responses, no network."""

    def __init__(self, script: list[Any] | Exception) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.script, Exception):
            raise self.script
        return self.script[len(self.calls) - 1]


def tool_use(name: str, input_: dict[str, Any], id_: str = "t1") -> Any:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def reply(*blocks: Any) -> Any:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def report(causes: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"summary": "R3020 jumps 70% in one month and no reason code is recorded for it.", "causes": causes, "next_steps": [{"action": "Ask the owner.", "who": "Avery Chen"}],
            "confidence": "medium", "limitations": [], **extra}  # fmt: skip


def GROUNDED(pid: int, status: str = "confirmed") -> list[dict[str, Any]]:  # noqa: N802
    return [
        {
            "statement": "The row has no recorded reason code for the jump.",
            "status": status,
            "evidence_refs": [f"project:{pid}"] if status == "confirmed" else [],
        }
    ]


async def test_claude_investigation_uses_tools_then_submits_a_checked_report(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(AVERY)
    target = await finding_for(http, "mom_anomaly", "R3020")
    pid = target["project_id"]
    fake = FakeAnthropic(
        [
            reply(
                tool_use("get_project_history", {"project_id": pid}, "a"),
                tool_use("get_project_owners", {"project_id": pid}, "b"),
            ),
            reply(
                tool_use(
                    "submit_investigation",
                    report(
                        [
                            {
                                "statement": "No reason code is recorded.",
                                "status": "confirmed",
                                "evidence_refs": [f"project:{pid}"],
                            },
                            {
                                "statement": "A made-up record proves it.",
                                "status": "confirmed",
                                "evidence_refs": ["revision:99999"],
                            },
                            {
                                "statement": "It may relate to a lost deal.",
                                "status": "hypothesis",
                                "evidence_refs": [],
                            },
                            {
                                "statement": "Cannot tell who changed it.",
                                "status": "insufficient_evidence",
                                "evidence_refs": [],
                            },
                        ],
                        financial_impact={
                            "amount": "999999999.00"
                        },  # a model's number is never used
                    ),
                    "c",
                )
            ),
        ]
    )
    use_ai(lambda _settings: fake)
    done = await investigate(http, headers, target["id"])

    assert (
        done["status"] == "completed"
        and done["mode"] == "ai"
        and done["model"] == "claude-sonnet-5-5"
    )
    assert done["fallback_reason"] is None
    causes = done["result"]["causes"]
    assert [c["status"] for c in causes] == [
        "confirmed",
        "hypothesis",
        "hypothesis",
        "insufficient_evidence",
    ]
    assert causes[0]["evidence_refs"] == [f"project:{pid}"]
    assert (
        causes[1]["evidence_refs"] == []
    )  # the invented reference was dropped and the claim downgraded
    assert done["result"]["financial_impact"]["amount"] == "21000.00"
    assert done["result"]["financial_impact"]["source"] == "deterministic"
    assert [s["tool"] for s in done["steps"]] == ["get_project_history", "get_project_owners"]

    first = fake.calls[0]
    assert {t["name"] for t in first["tools"]} == {
        "get_project_history",
        "compare_snapshots",
        "get_hubspot_deals",
        "find_duplicates",
        "get_project_owners",
        "submit_investigation",
    }  # read-only tools and the report; nothing that can change data
    assert "read-only" in first["system"] and "hypothesis" in first["system"]
    assert first["model"] == "claude-sonnet-5-5"
    first_prompt = first["messages"][0]["content"]
    assert (
        "avery@corp.test" not in first_prompt and "@" not in first_prompt
    )  # no email addresses are sent

    full = (await qa.session.execute(text("SELECT transcript FROM investigations"))).scalar_one()
    assert [t["role"] for t in full] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]  # prompts and outputs logged
    assert full[2]["usage"] == {"input": 100, "output": 50}


async def test_claude_can_only_see_what_the_requester_can_see(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(FIN)  # Finance may investigate and read history
    target = await finding_for(http, "stale_row", "R3010")
    pid = target["project_id"]
    other = (
        await qa.session.execute(text("SELECT id FROM projects WHERE source_row_id = 'R2001'"))
    ).scalar_one()
    fake = FakeAnthropic(
        [
            reply(tool_use("get_project_history", {"project_id": other}, "a")),
            reply(tool_use("submit_investigation", report(GROUNDED(pid)), "b")),
        ]
    )
    use_ai(lambda _s: fake)
    done = await investigate(http, headers, target["id"])
    assert (
        done["mode"] == "ai" and done["steps"][0]["outcome"] == "ok"
    )  # Finance may read any history
    assert pid


async def test_unknown_tool_requests_do_not_derail_claude(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    fake = FakeAnthropic(
        [
            reply(
                tool_use("edit_revenue", {"project_id": 1, "amount": 0}, "a"),
                tool_use("get_project_owners", {"project_id": target["project_id"]}, "o"),
            ),
            reply(tool_use("submit_investigation", report(GROUNDED(target["project_id"])), "b")),
        ]
    )
    use_ai(lambda _s: fake)
    done = await investigate(http, headers, target["id"])
    assert done["mode"] == "ai" and done["steps"][0] == {
        "tool": "edit_revenue",
        "arguments": {"project_id": 1, "amount": 0},
        "refs": [],
        "outcome": "unknown_tool",
    }
    assert (
        await qa.session.scalar(
            text("SELECT count(*) FROM revenue_revisions WHERE new_value = '0'")
        )
        == 0
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (RuntimeError("connection refused"), "RuntimeError"),
        ([reply(SimpleNamespace(type="text", text="I cannot do this"))], "without submitting"),
        (
            [
                reply(tool_use("get_project_owners", {"project_id": 1}, "o")),
                *[
                    reply(tool_use("submit_investigation", {"summary": "x"}, f"s{i}"))
                    for i in range(3)
                ],
            ],
            "invalid",
        ),
    ],
    ids=["api-error", "no-report", "invalid-report"],
)
async def test_any_claude_problem_falls_back_to_the_deterministic_investigator(
    qa: Seed, as_user: SignedIn, script: Any, expected: str
) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    use_ai(lambda _s: FakeAnthropic(script))
    done = await investigate(http, headers, target["id"])
    assert done["status"] == "completed" and done["mode"] == "deterministic"
    assert (
        expected in done["fallback_reason"] and "Claude was unavailable" in done["fallback_reason"]
    )
    assert (
        done["result"]["causes"]
        and done["result"]["financial_impact"]["amount"] == target["financial_impact"]
    )


PLACEHOLDER_REPORT = {
    "causes": [],
    "report": None,
    "summary": "placeholder",
    "confidence": "low",
    "next_steps": [],
}


async def test_a_placeholder_report_is_sent_back_and_the_model_corrects_it(
    qa: Seed, as_user: SignedIn
) -> None:
    """Regression: Claude submitted summary 'placeholder' with no causes and it was stored as the result."""
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "mom_anomaly", "R3020")
    pid = target["project_id"]
    fake = FakeAnthropic(
        [
            reply(tool_use("get_project_history", {"project_id": pid}, "h")),
            reply(tool_use("submit_investigation", PLACEHOLDER_REPORT, "p")),
            reply(tool_use("submit_investigation", report(GROUNDED(pid)), "r")),
        ]
    )
    use_ai(lambda _s: fake)
    done = await investigate(http, headers, target["id"])

    assert done["mode"] == "ai" and done["fallback_reason"] is None
    assert done["result"]["summary"] != "placeholder" and done["result"]["causes"]
    assert done["result"]["financial_impact"]["amount"] == "21000.00"
    rejected = fake.calls[2]["messages"][4]["content"][0]  # the model was told why, as a tool error
    assert rejected["is_error"] is True and rejected["tool_use_id"] == "p"
    assert "placeholder" in rejected["content"]


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (PLACEHOLDER_REPORT, "placeholder"),
        (report([]), "at least one cause"),
        (report(GROUNDED(0), summary="Too short."), "summary"),
        (
            report([{"statement": "TBD", "status": "hypothesis", "evidence_refs": []}]),
            "specific statement",
        ),
        (
            report(
                [
                    {
                        "statement": "A claim with a made-up record.",
                        "status": "confirmed",
                        "evidence_refs": ["revision:99999"],
                    }
                ]
            ),
            "retrieved",
        ),
        (report(GROUNDED(0), next_steps=[]), "next step"),
    ],
    ids=["placeholder", "no-causes", "short-summary", "tbd-cause", "ungrounded", "no-steps"],
)
async def test_a_report_that_stays_unusable_falls_back_with_a_real_explanation(
    qa: Seed, as_user: SignedIn, bad: dict[str, Any], why: str
) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "mom_anomaly", "R3020")
    pid = target["project_id"]
    if bad.get("summary") != "placeholder" and bad.get("causes") and "project:0" in str(bad):
        bad = {**bad, "causes": GROUNDED(pid)}
    fake = FakeAnthropic(
        [
            reply(tool_use("get_project_history", {"project_id": pid}, "h")),
            *[reply(tool_use("submit_investigation", bad, f"s{i}")) for i in range(3)],
        ]
    )
    use_ai(lambda _s: fake)
    done = await investigate(http, headers, target["id"])

    assert done["status"] == "completed" and done["mode"] == "deterministic"
    assert "rejected" in done["fallback_reason"] and why in done["fallback_reason"]
    result = done["result"]
    assert result["summary"] != "placeholder" and len(result["summary"]) > 40 and result["causes"]
    assert result["financial_impact"]["source"] == "deterministic"
    assert len(fake.calls) == 4  # the model got two chances to repair it, then we stopped


async def test_a_report_submitted_before_the_lookups_return_is_not_accepted(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "mom_anomaly", "R3020")
    pid = target["project_id"]
    fake = FakeAnthropic(
        [
            reply(  # looks things up and submits in the same turn, before seeing any result
                tool_use("get_project_history", {"project_id": pid}, "h"),
                tool_use("submit_investigation", PLACEHOLDER_REPORT, "p"),
            ),
            reply(tool_use("submit_investigation", report(GROUNDED(pid)), "r")),
        ]
    )
    use_ai(lambda _s: fake)
    done = await investigate(http, headers, target["id"])

    assert done["mode"] == "ai" and done["result"]["summary"] != "placeholder"
    ids = {r["tool_use_id"] for r in fake.calls[1]["messages"][2]["content"]}
    assert ids == {"h", "p"}  # every tool call is answered, and the early submit was refused


async def test_the_model_is_shown_the_strict_report_schema_and_rules(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "mom_anomaly", "R3020")
    pid = target["project_id"]
    fake = FakeAnthropic(
        [
            reply(tool_use("get_project_history", {"project_id": pid}, "h")),
            reply(tool_use("submit_investigation", report(GROUNDED(pid)), "r")),
        ]
    )
    use_ai(lambda _s: fake)
    await investigate(http, headers, target["id"])
    schema = next(t for t in fake.calls[0]["tools"] if t["name"] == "submit_investigation")[
        "input_schema"
    ]
    assert schema["properties"]["causes"]["minItems"] == 1
    assert schema["properties"]["summary"]["minLength"] >= 40
    assert "never placeholder" in fake.calls[0]["system"]


async def test_the_model_running_out_of_steps_falls_back(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    loop = [
        reply(tool_use("get_project_owners", {"project_id": target["project_id"]}, f"x{i}"))
        for i in range(10)
    ]
    use_ai(lambda _s: FakeAnthropic(loop))
    done = await investigate(http, headers, target["id"])
    assert done["mode"] == "deterministic" and "allowed number of steps" in done["fallback_reason"]


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        ({"anthropic_api_key": None, "ai_data_use_approved": True}, "No Anthropic API key"),
        ({"anthropic_api_key": "sk-test", "ai_data_use_approved": False}, "data-use approval"),
    ],
)
async def test_claude_is_never_called_without_a_key_and_data_use_approval(
    qa: Seed, as_user: SignedIn, settings: dict[str, Any], reason: str
) -> None:
    called: list[object] = []

    def factory(_settings: object) -> object:
        called.append(1)
        return FakeAnthropic([])

    app.dependency_overrides[get_settings] = lambda: make_settings(**settings)
    app.dependency_overrides[get_ai_client_factory] = lambda: factory
    http, headers = await as_user(ADMIN)
    target = await finding_for(http, "stale_row", "R3010")
    detail = await get(http, f"/v1/qa/findings/{target['id']}")
    assert detail["ai_available"] is False and reason in detail["ai_unavailable_reason"]
    done = await investigate(http, headers, target["id"])
    assert done["mode"] == "deterministic" and reason in done["fallback_reason"] and called == []


async def test_the_monthly_ai_call_limit_is_enforced(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    findings = (await get(http, "/v1/qa/findings"))["items"][:3]

    def script() -> FakeAnthropic:
        ok = report(GROUNDED(0, status="insufficient_evidence"))
        return FakeAnthropic(
            [
                reply(tool_use("get_project_owners", {"project_id": 1}, "o")),
                reply(tool_use("submit_investigation", ok, "a")),
            ]
        )

    use_ai(lambda _s: script(), ai_monthly_call_limit=2)
    modes = [(await investigate(http, headers, f["id"]))["mode"] for f in findings]
    assert modes == ["ai", "ai", "deterministic"]
    last = await get(http, f"/v1/qa/findings/{findings[2]['id']}")
    assert "monthly limit of 2" in last["investigations"][0]["fallback_reason"]


# ---- Forecast Health -----------------------------------------------------------------------------------


async def test_health_summarises_open_findings_with_stated_formulas(
    qa: Seed, as_user: SignedIn
) -> None:
    http, _ = await as_user(CRO)
    health = await get(http, "/v1/qa/health")
    items = (await get(http, "/v1/qa/findings"))["items"]

    assert health["open_total"] == 20 and health["unassigned_open"] == 2
    assert health["open_by_rule"] == {
        "duplicate_project": 4,
        "stale_row": 3,
        "mom_anomaly": 4,
        "missing_required": 4,
        "closed_won_missing": 3,
        "amount_mismatch": 2,
    }
    assert sum(health["open_by_severity"].values()) == 20
    assert Decimal(health["revenue_at_stake"]) == sum(Decimal(i["financial_impact"]) for i in items)
    assert health["status"] == "red" and any("error finding" in r for r in health["status_reasons"])
    assert health["can_run"] is True and health["last_success"]["findings_open"] == 20
    assert health["checks_overdue"] is False  # the run just happened
    assert all(m["formula"] for m in health["metrics"])
    owners = {o["name"]: o for o in health["owners"]}
    assert (
        "Unassigned (needs an owner)" in owners
        and owners["Unassigned (needs an owner)"]["open_findings"] == 2
    )
    counts = [o["open_findings"] for o in health["owners"]]
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) == 20
    metrics = {m["key"]: m["value"] for m in health["metrics"]}
    assert (
        metrics["open"] == "20"
        and metrics["unassigned"] == "2"
        and metrics["triage_rate"] == "0%"
        and metrics["stale_rows"] == "3"
    )


async def test_health_triage_rate_and_awaiting_review_follow_decisions(
    qa: Seed, as_user: SignedIn
) -> None:
    http, headers = await as_user(ADMIN)
    items = (await get(http, "/v1/qa/findings"))["items"]
    for item in items[:5]:
        assert (
            await http.post(
                f"/v1/qa/findings/{item['id']}/dismiss",
                json={"reason": "Known and accepted"},
                headers=headers,
            )
        ).status_code == 200
    await investigate(http, headers, items[5]["id"])
    health = await get(http, "/v1/qa/health")
    metrics = {m["key"]: m["value"] for m in health["metrics"]}
    assert (
        health["open_total"] == 15
        and metrics["triage_rate"] == "25%"
        and health["awaiting_review"] == 1
    )


async def test_health_when_nothing_has_run_yet(
    client: AsyncClient, session: AsyncSession, as_user: SignedIn, builder: WorkbookBuilder
) -> None:
    seed = await create_seed(session, builder)
    http, _ = await as_user(CRO)
    health = await get(http, "/v1/qa/health")
    assert health["open_total"] == 0 and health["status"] == "amber" and health["last_run"] is None
    assert "never run" in " ".join(health["status_reasons"]) and seed
    assert (await get(http, "/v1/qa/findings"))["items"] == []


async def test_health_is_green_when_clean_and_current(qa: Seed, as_user: SignedIn) -> None:
    await qa.session.execute(
        text(
            "UPDATE qa_findings SET status = 'dismissed', reviewed_by_id = 1, reviewed_at = now(), review_reason = 'seeded test'"
        )
    )
    await qa.session.execute(text("UPDATE qa_runs SET finished_at = now()"))
    await qa.session.commit()
    http, _ = await as_user(CRO)
    health = await get(http, "/v1/qa/health")
    assert (
        health["status"] == "green"
        and health["open_total"] == 0
        and health["checks_overdue"] is False
    )
    assert health["status_reasons"] == ["No open findings and the checks are current."]


async def test_health_goes_red_when_the_latest_run_failed(qa: Seed, as_user: SignedIn) -> None:
    qa.session.add(
        QaRun(
            trigger="scheduled",
            status="failed",
            started_at=NOW,
            finished_at=NOW,
            triggered_by_label="nightly job",
            error_message="boom",
        )
    )
    await qa.session.commit()
    http, _ = await as_user(CRO)
    health = await get(http, "/v1/qa/health")
    assert health["status"] == "red" and "The latest QA run failed." in health["status_reasons"]
    assert (
        health["last_run"]["status"] == "failed" and health["last_success"]["status"] == "completed"
    )


async def test_audit_trail_covers_every_action_in_order(qa: Seed, as_user: SignedIn) -> None:
    http, headers = await as_user(ADMIN)
    target = (await get(http, "/v1/qa/findings"))["items"][0]
    await investigate(http, headers, target["id"])
    await http.post(
        f"/v1/qa/findings/{target['id']}/accept", json={"reason": "Confirmed real"}, headers=headers
    )
    events = list(
        await qa.session.scalars(
            select(QaFindingEvent)
            .where(QaFindingEvent.finding_id == target["id"])
            .order_by(QaFindingEvent.id)
        )
    )
    assert [e.event_type for e in events] == [
        "created",
        "investigation_started",
        "investigated",
        "accepted",
    ]
    assert events[-1].actor_label == "Ada Admin" and events[-1].actor_user_id is not None
    assert date.today() and events[0].actor_label == "qa-engine"
