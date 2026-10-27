"""Explicitly provision (or revoke) application users: `uv run python -m app.provisioning --help`.

Only users provisioned here can sign in. Provisioning is never inferred from an email domain, and
the people imported from the workbook stay unprovisioned until an operator runs `grant` for them.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.roles import ALL_ROLES, canonical_role
from app.models import Role, User

EXIT_OK, EXIT_ERROR = 0, 1
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class ProvisioningError(Exception):
    pass


async def _find(session: AsyncSession, code: str | None, email: str | None) -> User | None:
    query = select(User).options(selectinload(User.roles))
    if code:
        user = await session.scalar(query.where(User.code == code))
        if user is None:
            raise ProvisioningError(f"No imported person has code '{code}'.")
        return user
    if email:
        return await session.scalar(query.where(func.lower(User.email) == email.lower()))
    raise ProvisioningError("Give --email or --code.")


async def grant(
    session: AsyncSession, *, email: str, roles: list[str], code: str | None, name: str | None
) -> User:
    email = email.strip().lower()
    if not EMAIL.fullmatch(email):
        raise ProvisioningError(f"'{email}' is not a valid email address.")
    role_names = sorted({canonical_role(r) for r in roles})
    unknown = [r for r in role_names if r not in ALL_ROLES]
    if unknown:
        raise ProvisioningError(f"Unknown role(s): {', '.join(unknown)}. Valid: {list(ALL_ROLES)}")
    if not role_names:
        raise ProvisioningError("Give at least one --role.")

    user = await _find(session, code, None if code else email)
    holder = await session.scalar(select(User).where(func.lower(User.email) == email))
    if holder is not None and (user is None or holder.id != user.id):
        raise ProvisioningError(f"The email {email} already belongs to another person.")
    if user is None:
        if not name:
            raise ProvisioningError(
                "This email is new: give --name (or --code of an imported person)."
            )
        user = User(name=name, roles=[])
        session.add(user)

    role_rows = {r.name: r for r in await session.scalars(select(Role))}
    for role_name in role_names:
        if role_name not in role_rows:
            role_rows[role_name] = Role(name=role_name)
            session.add(role_rows[role_name])
    user.email = email
    user.roles = [role_rows[r] for r in role_names]
    user.is_provisioned = True
    await session.commit()
    return user


async def revoke(
    session: AsyncSession, *, email: str | None, code: str | None, reset_identity: bool
) -> User:
    user = await _find(session, code, email)
    if user is None:
        raise ProvisioningError("No such user.")
    user.is_provisioned = False
    if reset_identity:
        user.google_sub = None
    await session.commit()
    return user


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.scalars(
        select(User)
        .options(selectinload(User.roles))
        .order_by(User.is_provisioned.desc(), User.name)
    )
    return list(result)


def _describe(user: User) -> str:
    state = "PROVISIONED" if user.is_provisioned else "not provisioned"
    bound = "google-bound" if user.google_sub else "unbound"
    roles = ", ".join(r.name for r in user.roles) or "no roles"
    code, email = user.code or "-", user.email or "-"
    return f"{state:<16} {code:<7} {user.name:<22} {email:<34} {roles} [{bound}]"


async def _run(args: argparse.Namespace) -> int:
    from app.database import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            if args.command == "grant":
                user = await grant(
                    session, email=args.email, roles=args.role, code=args.code, name=args.name
                )
                print("Provisioned:\n  " + _describe(user))
            elif args.command == "revoke":
                user = await revoke(
                    session, email=args.email, code=args.code, reset_identity=args.reset_identity
                )
                print("Revoked (can no longer sign in):\n  " + _describe(user))
            else:
                for user in await list_users(session):
                    print(_describe(user))
    except ProvisioningError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await engine.dispose()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.provisioning",
        description="Provision or revoke application users (who may sign in, with which roles).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    grant_cmd = commands.add_parser(
        "grant",
        help="allow a person to sign in with the given roles",
        description="Roles replace any existing roles. Use --code to attach a real Google address "
        "to a person imported from the workbook (so their project ownership applies); otherwise "
        "give --name to create a new person.",
    )
    grant_cmd.add_argument("--email", required=True, help="their verified Google Workspace email")
    grant_cmd.add_argument("--role", action="append", default=[], help=f"one of {list(ALL_ROLES)}")
    grant_cmd.add_argument("--code", help="workbook user code, e.g. AC, to adopt that person")
    grant_cmd.add_argument("--name", help="display name when creating a new person")

    revoke_cmd = commands.add_parser("revoke", help="stop a person signing in")
    revoke_cmd.add_argument("--email")
    revoke_cmd.add_argument("--code")
    revoke_cmd.add_argument(
        "--reset-identity", action="store_true", help="also forget the bound Google account"
    )

    commands.add_parser("list", help="show everyone and their provisioning state")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
