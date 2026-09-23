"""Invite-only household: Neon Auth accounts plus the `household_member` allowlist.

Accounts are created here (sign-up stays disabled in Neon Auth). An invited
person signs in to the app with a one-time code emailed by Neon Auth.
"""

import json
import logging
import subprocess
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session

from castle_chef.models import HouseholdMember
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)


class NeonApiError(Exception):
    pass


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


def invite(session: Session, email: str, name: str) -> UUID:
    """Create the Neon Auth account if needed and add it to the household."""
    user_id = _auth_user_id(session, email)
    created_account = user_id is None
    if user_id is None:
        output = _neon_api("/auth/users", "POST", {"email": email, "name": name})
        user_id = UUID(json.loads(output)["id"])
    if session.get(HouseholdMember, user_id) is None:
        session.add(HouseholdMember(user_id=user_id))
        session.commit()
    log.info(
        "family.invited",
        extra={"email": email, "user_id": str(user_id), "created_account": created_account},
    )
    return user_id


def remove(session: Session, email: str) -> None:
    """Remove from the household and delete the Neon Auth account."""
    user_id = _auth_user_id(session, email)
    if user_id is None:
        raise LookupError(f"No account for {email}.")
    member = session.get(HouseholdMember, user_id)
    if member is not None:
        session.delete(member)
        session.commit()
    _neon_api(f"/auth/users/{user_id}", "DELETE")
    log.info("family.removed", extra={"email": email, "user_id": str(user_id)})


def members(session: Session) -> list[tuple[str, str]]:
    """(email, name) of everyone in the household, in invite order."""
    rows = session.execute(
        text(
            "SELECT u.email, u.name FROM household_member AS m "
            'JOIN neon_auth."user" AS u ON u.id = m.user_id ORDER BY m.invited_at'
        )
    ).all()
    return [(row.email, row.name) for row in rows]
