"""Edit and history permissions. Pure functions over already-loaded models.

PRD FR-03: a project can be edited only by its assigned Client Partner, AE or Commercial Owner,
plus Sales leadership and Admin; every other role is read-only.
"""

from dataclasses import dataclass

from app.auth.roles import (
    EDIT_ANY_ROLES,
    HISTORY_ANY_ROLES,
    OWNER_ROLE_LABELS,
    OWNER_ROLE_TO_USER_ROLE,
)
from app.models import Project, User


@dataclass(frozen=True)
class EditDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None


def role_names(user: User) -> set[str]:
    return {role.name for role in user.roles}


def owner_summary(project: Project) -> list[dict[str, str]]:
    return [
        {"name": o.user.name, "role": OWNER_ROLE_LABELS.get(o.role, o.role)} for o in project.owners
    ]


def _owners_sentence(project: Project) -> str:
    owners = owner_summary(project)
    if not owners:
        return "it has no assigned owner"
    return "it is owned by " + ", ".join(f"{o['name']} ({o['role']})" for o in owners)


def is_assigned_owner(user: User, project: Project) -> bool:
    roles = role_names(user)
    return any(
        owner.user_id == user.id and OWNER_ROLE_TO_USER_ROLE.get(owner.role) in roles
        for owner in project.owners
    )


def edit_decision(user: User, project: Project) -> EditDecision:
    roles = role_names(user)
    if roles & EDIT_ANY_ROLES or is_assigned_owner(user, project):
        return EditDecision(True)
    label = f"{project.customer.name} - {project.name}"
    if roles & set(OWNER_ROLE_TO_USER_ROLE.values()):
        return EditDecision(
            False,
            "not_project_owner",
            f"You cannot edit {label}: {_owners_sentence(project)}. Only its owners, "
            "Sales leadership or an Administrator can change it.",
        )
    return EditDecision(
        False,
        "read_only_role",
        f"Your role is read-only for forecasts, so you cannot edit {label} "
        f"({_owners_sentence(project)}).",
    )


def can_view_history(user: User, project: Project) -> bool:
    return bool(role_names(user) & HISTORY_ANY_ROLES) or edit_decision(user, project).allowed
