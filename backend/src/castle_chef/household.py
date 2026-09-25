"""Households and who is in them: Neon Auth accounts plus `household_member`.

The app is invite-only. Accounts are created here (sign-up stays disabled in
Neon Auth), and each account belongs to at most one household, whose jobs and
meals are the only ones it sees. An invited person signs in to the app with a
one-time code emailed by Neon Auth.
"""

import json
import logging
import subprocess
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, col, func, select

from castle_chef.models import GroceryOrder, Household, HouseholdMember
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)


class NeonApiError(Exception):
    pass


class HouseholdError(Exception):
    """A household command can't be carried out as asked (the message says why)."""


def _neon_api(path: str, method: str, body: dict | None = None) -> str:
    settings = get_settings()
    if not settings.neon_project_id or not settings.neon_branch_id:
        raise NeonApiError(
            "Set CASTLE_CHEF_NEON_PROJECT_ID and CASTLE_CHEF_NEON_BRANCH_ID (see backend/.env)."
        )
    command = [
        "neonctl", "api",
        f"/projects/{settings.neon_project_id}/branches/{settings.neon_branch_id}{path}",
        "-X", method,
    ]  # fmt: skip
    if body is not None:
        command += ["-d", json.dumps(body)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise NeonApiError(f"neonctl {method} {path} failed: {result.stderr.strip()}")
    return result.stdout


def _auth_user_id(session: Session, email: str) -> UUID | None:
    return session.execute(
        text('SELECT id FROM neon_auth."user" WHERE lower(email) = lower(:email)'),
        {"email": email},
    ).scalar_one_or_none()


def create(session: Session, name: str) -> Household:
    """Create an empty household."""
    if session.exec(select(Household).where(Household.name == name)).first() is not None:
        raise HouseholdError(f"A household named {name!r} already exists.")
    household = Household(name=name)
    session.add(household)
    session.commit()
    session.refresh(household)
    log.info("household.created", extra={"household": name, "household_id": str(household.id)})
    return household


def get(session: Session, name: str) -> Household:
    """The household with this name."""
    household = session.exec(select(Household).where(Household.name == name)).first()
    if household is None:
        raise HouseholdError(
            f"No household named {name!r}. See `castle-chef household list`, or create it "
            "with `castle-chef household create`."
        )
    return household


def households(session: Session) -> list[tuple[str, int, int]]:
    """(name, member count, order count) of every household, by name."""
    members = (
        select(func.count())
        .select_from(HouseholdMember)
        .where(HouseholdMember.household_id == Household.id)
        .scalar_subquery()
    )
    grocery_orders = (
        select(func.count())
        .select_from(GroceryOrder)
        .where(GroceryOrder.household_id == Household.id)
    ).scalar_subquery()
    rows = session.exec(
        select(Household.name, members, grocery_orders).order_by(col(Household.name))
    ).all()
    return [(name, member_count, order_count) for name, member_count, order_count in rows]


def invite(session: Session, email: str, name: str, household_name: str) -> UUID:
    """Create the Neon Auth account if needed and add it to the household. Someone
    already in another household must be removed from it first."""
    household = get(session, household_name)
    user_id = _auth_user_id(session, email)
    membership = session.get(HouseholdMember, user_id) if user_id is not None else None
    if membership is not None and membership.household_id != household.id:
        other = session.exec(
            select(Household.name).where(Household.id == membership.household_id)
        ).one()
        raise HouseholdError(
            f"{email} is already in the household {other!r}. Remove them first "
            "(`castle-chef household remove` also deletes their account)."
        )
    created_account = user_id is None
    if user_id is None:
        output = _neon_api("/auth/users", "POST", {"email": email, "name": name})
        user_id = UUID(json.loads(output)["id"])
    if membership is None:
        session.add(HouseholdMember(user_id=user_id, household_id=household.id))
    session.commit()
    log.info(
        "household.invited",
        extra={
            "email": email,
            "user_id": str(user_id),
            "household": household.name,
            "created_account": created_account,
        },
    )
    return user_id


def remove(session: Session, email: str) -> None:
    """Remove from their household and delete the Neon Auth account."""
    user_id = _auth_user_id(session, email)
    if user_id is None:
        raise HouseholdError(f"No account for {email}.")
    member = session.get(HouseholdMember, user_id)
    if member is not None:
        session.delete(member)
        session.commit()
    _neon_api(f"/auth/users/{user_id}", "DELETE")
    log.info("household.removed", extra={"email": email, "user_id": str(user_id)})


def members(session: Session, household_name: str) -> list[tuple[str, str]]:
    """(email, name) of everyone in the household, in invite order."""
    household = get(session, household_name)
    rows = session.execute(
        text(
            "SELECT u.email, u.name FROM household_member AS m "
            'JOIN neon_auth."user" AS u ON u.id = m.user_id '
            "WHERE m.household_id = :household_id ORDER BY m.invited_at"
        ),
        {"household_id": household.id},
    ).all()
    return [(row.email, row.name) for row in rows]
