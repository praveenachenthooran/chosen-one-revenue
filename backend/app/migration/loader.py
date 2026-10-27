"""Idempotent loader: writes a ParsedWorkbook into PostgreSQL and reconciles the totals.

Projects are keyed by the workbook's own Row ID, so repeating an import updates in place.
A repeat run over an unchanged workbook changes no rows and writes no audit events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.roles import ALL_ROLES, canonical_role
from app.migration.workbook import CATEGORY_DESCRIPTIONS, ParsedProject, ParsedWorkbook
from app.models import (
    Category,
    Customer,
    ImportIssue,
    ImportReconciliation,
    ImportRun,
    Project,
    ProjectMonthlyRevenue,
    ProjectOwner,
    RevenueRevision,
    Role,
    User,
)

ACTOR_LABEL = "excel-import"
PROJECT_FIELDS = (
    "name",
    "practice",
    "region",
    "industry",
    "country",
    "billing_type",
    "category_code",
    "status",
    "source_last_updated",
    "source_last_updated_by",
    "source_row_number",
)


@dataclass
class ImportReport:
    run_id: int
    file_name: str
    rows_read: int
    projects_created: int
    projects_updated: int
    projects_unchanged: int
    rows_rejected: int
    warnings: int
    reconciled: bool
    reconciliation: list[ImportReconciliation]
    issues: list[ImportIssue]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


async def _sync_reference_data(session: AsyncSession, parsed: ParsedWorkbook) -> dict[str, User]:
    existing_categories = set(await session.scalars(select(Category.code)))
    for code, description in CATEGORY_DESCRIPTIONS.items():
        if code not in existing_categories:
            session.add(Category(code=code, description=description))

    roles = {r.name: r for r in await session.scalars(select(Role))}
    wanted_roles = set(ALL_ROLES) | {canonical_role(r) for u in parsed.users for r in u.roles}
    for role_name in sorted(wanted_roles):
        if role_name not in roles:
            roles[role_name] = Role(name=role_name)
            session.add(roles[role_name])

    users = {
        u.code: u
        for u in await session.scalars(select(User).options(selectinload(User.roles)))
        if u.code
    }
    for source in parsed.users:
        user = users.get(source.code)
        if user is None:
            # Imported people are NOT authorized application users (is_provisioned stays false).
            user = User(code=source.code, name=source.name, roles=[])
            session.add(user)
            users[source.code] = user
        if user.is_provisioned:
            # Provisioning is an explicit administrative act: a re-import must never replace the
            # email, roles or name that an administrator set for a signed-in person.
            continue
        user.name, user.location, user.notes = source.name, source.location, source.notes
        if source.email != user.email:
            user.email = source.email
        user.roles = [roles[canonical_role(r)] for r in source.roles]
    await session.flush()
    return users


def _audit(
    run: ImportRun, project: Project, field: str, change_type: str, old: object, new: object,
    month: date | None = None, note: str | None = None,
) -> RevenueRevision:  # fmt: skip
    return RevenueRevision(
        project_id=project.id, month=month, field=field, change_type=change_type,
        old_value=_text(old), new_value=_text(new), actor_label=ACTOR_LABEL, import_run_id=run.id,
        note=note,
    )  # fmt: skip


async def _upsert_project(
    session: AsyncSession,
    run: ImportRun,
    source: ParsedProject,
    existing: Project | None,
    customers: dict[str, Customer],
    users: dict[str, User],
    revenue: dict[date, ProjectMonthlyRevenue],
    kept_edits: list[ImportIssue],
) -> str:
    """Create or update one project. Returns 'created', 'updated' or 'unchanged'.

    Each month keeps two values: `baseline_amount` (what the workbook says, kept in step with
    the workbook) and `amount` (the current forecast, which people may edit in the app). A month
    whose current value differs from its old baseline was edited in the app, so a changed
    workbook updates the baseline but never overwrites that edit; it is reported instead.
    """
    customer = customers.get(source.customer)
    if customer is None:
        customer = Customer(name=source.customer)
        session.add(customer)
        customers[source.customer] = customer
        await session.flush()

    wanted = {
        "name": source.name,
        "practice": source.practice,
        "region": source.region,
        "industry": source.industry,
        "country": source.country,
        "billing_type": source.billing_type,
        "category_code": source.category,
        "status": source.status,
        "source_last_updated": source.last_updated,
        "source_last_updated_by": source.last_updated_by,
        "source_row_number": source.row_number,
    }
    audits: list[RevenueRevision] = []
    changed = False

    if existing is None:
        project = Project(
            source_row_id=source.source_row_id,
            customer_id=customer.id,
            name=source.name,
            practice=source.practice,
            region=source.region,
            industry=source.industry,
            country=source.country,
            billing_type=source.billing_type,
            category_code=source.category,
            status=source.status,
            source_last_updated=source.last_updated,
            source_last_updated_by=source.last_updated_by,
            source_row_number=source.row_number,
            owners=[],
        )
        session.add(project)
        await session.flush()
        audits.append(_audit(run, project, "project", "create", None, source.source_row_id))
        project_owners: dict[str, ProjectOwner] = {}
        status = "created"
    else:
        project = existing
        project_owners = {o.role: o for o in project.owners}
        status = "unchanged"
        if project.customer_id != customer.id:
            audits.append(
                _audit(run, project, "customer", "update", project.customer_id, customer.id)
            )
            project.customer_id = customer.id
            changed = True
        for name in PROJECT_FIELDS:
            old, new = getattr(project, name), wanted[name]
            if old != new:
                if name != "source_row_number":
                    audits.append(_audit(run, project, name, "update", old, new))
                setattr(project, name, new)
                changed = True

    # Owners
    wanted_owners = {role: users[code] for role, code in source.owner_codes.items()}
    for role, owner in project_owners.items():
        if role not in wanted_owners:
            audits.append(_audit(run, project, f"owner:{role}", "delete", owner.user.code, None))
            project.owners.remove(owner)
            changed = True
    for role, user in wanted_owners.items():
        current = project_owners.get(role)
        if current is None:
            project.owners.append(ProjectOwner(role=role, user=user))
            if existing is not None:
                audits.append(_audit(run, project, f"owner:{role}", "create", None, user.code))
                changed = True
        elif current.user_id != user.id:
            audits.append(
                _audit(run, project, f"owner:{role}", "update", current.user.code, user.code)
            )
            current.user = user
            changed = True

    # Monthly revenue: exact decimal comparison, never rounded.
    def kept(month: date, message: str, value: object) -> None:
        kept_edits.append(
            ImportIssue(
                run_id=run.id, severity="warning", code="edited_in_app_kept", sheet="Forecast",
                source_row_id=source.source_row_id, field=f"{month:%Y-%m}", value=_text(value),
                message=message,
            )
        )  # fmt: skip

    for month, amount in source.months.items():
        row = revenue.get(month)
        if row is None:
            session.add(
                ProjectMonthlyRevenue(
                    project_id=project.id, month=month, amount=amount, baseline_amount=amount,
                    source_cell=source.cells.get(month),
                )
            )  # fmt: skip
            if existing is not None:
                audits.append(_audit(run, project, "revenue", "create", None, amount, month))
                changed = True
        elif row.baseline_amount != amount:
            old_baseline, unedited = row.baseline_amount, row.amount == row.baseline_amount
            row.baseline_amount, row.source_cell = amount, source.cells.get(month)
            if unedited:
                audits.append(_audit(run, project, "revenue", "update", row.amount, amount, month))
                row.amount = amount
            else:
                audits.append(
                    _audit(run, project, "baseline", "update", old_baseline, amount, month,
                           "Workbook value changed; the in-app edit was kept")
                )  # fmt: skip
                kept(
                    month,
                    f"{month:%b %Y}: the workbook changed to {amount} but the value {row.amount} "
                    "edited in the app was kept",
                    amount,
                )
            changed = True
    for month, row in revenue.items():
        if month in source.months or row.baseline_amount is None:
            continue  # still in the workbook, or a month that exists only in the app
        if row.amount == row.baseline_amount:
            audits.append(_audit(run, project, "revenue", "delete", row.amount, None, month))
            await session.delete(row)
        else:
            audits.append(
                _audit(run, project, "baseline", "update", row.baseline_amount, None, month,
                       "Month is blank in the workbook now; the in-app edit was kept")
            )  # fmt: skip
            kept(
                month,
                f"{month:%b %Y} is blank in the workbook now but the value {row.amount} "
                "edited in the app was kept",
                None,
            )
            row.baseline_amount = None
        changed = True

    session.add_all(audits)
    if status == "unchanged" and changed:
        status = "updated"
    return status


async def _reconcile(
    session: AsyncSession, run: ImportRun, parsed: ParsedWorkbook, imported_ids: list[str]
) -> list[ImportReconciliation]:
    # Compared with the baseline column: what the import loaded, whatever edits came later.
    totals: dict[date, Decimal] = {}
    if imported_ids:
        rows = await session.execute(
            select(ProjectMonthlyRevenue.month, func.sum(ProjectMonthlyRevenue.baseline_amount))
            .join(Project, Project.id == ProjectMonthlyRevenue.project_id)
            .where(Project.source_row_id.in_(imported_ids))
            .group_by(ProjectMonthlyRevenue.month)
        )
        totals = {month: Decimal(total or 0) for month, total in rows}

    results: list[ImportReconciliation] = []
    for month in parsed.month_columns:
        database_total = totals.get(month, Decimal("0.00"))
        source_total = parsed.source_totals[month]
        excel_total = parsed.excel_totals.get(month)
        matches = database_total == source_total and (
            excel_total is None or database_total == excel_total
        )
        results.append(
            ImportReconciliation(
                run_id=run.id, month=month, source_total=source_total,
                database_total=database_total, excel_reported_total=excel_total, matches=matches,
            )
        )  # fmt: skip
    session.add_all(results)
    return results


async def import_workbook(session: AsyncSession, parsed: ParsedWorkbook) -> ImportReport:
    """Import in a single transaction; on any error nothing is committed except a failed run row."""
    started_at = datetime.now(UTC)
    run = ImportRun(
        source_file=parsed.file_name,
        source_sha256=parsed.sha256,
        source_as_of=parsed.as_of,
        started_at=started_at,
        status="running",
        month_columns=[m.isoformat() for m in parsed.month_columns],
        rows_read=parsed.rows_read,
    )
    session.add(run)
    await session.flush()
    run_id = run.id

    try:
        users = await _sync_reference_data(session, parsed)
        customers = {c.name: c for c in await session.scalars(select(Customer))}
        existing = {
            p.source_row_id: p
            for p in await session.scalars(
                select(Project).options(
                    selectinload(Project.owners).selectinload(ProjectOwner.user)
                )
            )
        }
        revenue_by_project: dict[int, dict[date, ProjectMonthlyRevenue]] = {}
        for row in await session.scalars(select(ProjectMonthlyRevenue)):
            revenue_by_project.setdefault(row.project_id, {})[row.month] = row

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        kept_edits: list[ImportIssue] = []
        for source in parsed.projects:
            current = existing.get(source.source_row_id)
            revenue = revenue_by_project.get(current.id, {}) if current else {}
            outcome = await _upsert_project(
                session, run, source, current, customers, users, revenue, kept_edits
            )
            counts[outcome] += 1
        await session.flush()

        issues = [
            ImportIssue(
                run_id=run.id, severity=i.severity, code=i.code, sheet=i.sheet,
                row_number=i.row_number, source_row_id=i.source_row_id, field=i.field,
                value=i.value, message=i.message, row_rejected=i.row_rejected, details=i.details,
            )
            for i in parsed.issues
        ]  # fmt: skip
        issues.extend(kept_edits)
        session.add_all(issues)
        reconciliation = await _reconcile(
            session, run, parsed, [p.source_row_id for p in parsed.projects]
        )

        run.projects_created = counts["created"]
        run.projects_updated = counts["updated"]
        run.projects_unchanged = counts["unchanged"]
        run.rows_rejected = parsed.rejected_row_count
        run.warnings = sum(1 for i in issues if i.severity == "warning")
        run.reconciled = all(r.matches for r in reconciliation)
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        await session.rollback()
        session.add(
            ImportRun(
                source_file=parsed.file_name, source_sha256=parsed.sha256,
                started_at=started_at, finished_at=datetime.now(UTC), status="failed",
                rows_read=parsed.rows_read,
            )
        )  # fmt: skip
        await session.commit()
        raise

    return ImportReport(
        run_id=run_id,
        file_name=parsed.file_name,
        rows_read=parsed.rows_read,
        projects_created=counts["created"],
        projects_updated=counts["updated"],
        projects_unchanged=counts["unchanged"],
        rows_rejected=run.rows_rejected,
        warnings=run.warnings,
        reconciled=bool(run.reconciled),
        reconciliation=reconciliation,
        issues=issues,
    )
