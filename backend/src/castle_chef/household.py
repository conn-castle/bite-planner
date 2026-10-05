"""Households and who is in them: Neon Auth accounts plus `app_user`.

The app is invite-only. A member (or the admin command) invites someone by email;
they join when they first sign in with it, their emailed code creating the
account. Only an invited email can create an account: Neon Auth asks the API
before it makes one (its `user.before_create` webhook: `may_sign_up`). Each
account belongs to at most one household, whose orders and meals are the only
ones it sees. Removing someone ends their membership and keeps the account, so
their name stays on what they did, and inviting them again works.
"""

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy import func as sql_func
from sqlmodel import Session, col, func, select

from castle_chef import orders, reminders
from castle_chef.db import for_update
from castle_chef.models import AppUser, GroceryOrder, Household, HouseholdInvite

log = logging.getLogger(__name__)

# Enough to catch a typo; the emailed code proves the rest.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_LENGTH = 60
EMAIL_LENGTH = 254


class HouseholdError(Exception):
    """A household command can't be carried out as asked (the message says why)."""


class NotFound(HouseholdError):
    """No such member or invitation in the household."""


class Refused(HouseholdError):
    """It would leave things as they can't be: the last member removed, or someone
    in two households."""


class Invalid(HouseholdError):
    """An invitation's email or name isn't usable."""


def _auth_user_id(session: Session, email: str) -> UUID | None:
    return session.execute(
        text('SELECT id FROM neon_auth."user" WHERE lower(email) = lower(:email)'),
        {"email": email},
    ).scalar_one_or_none()


def _account_email(session: Session, user_id: UUID) -> str | None:
    return session.execute(
        text('SELECT email FROM neon_auth."user" WHERE id = :id'), {"id": user_id}
    ).scalar_one_or_none()


def _invite_for(session: Session, email: str, lock: bool = False) -> HouseholdInvite | None:
    query = select(HouseholdInvite).where(sql_func.lower(HouseholdInvite.email) == email.lower())
    return session.exec(for_update(query) if lock else query).first()


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
        .select_from(AppUser)
        .where(AppUser.household_id == Household.id)
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


def invite(
    session: Session, household: Household, email: str, name: str, invited_by: UUID | None
) -> HouseholdInvite:
    """Invite someone by email (and the name to call them): they join when they
    first sign in with it. Inviting the same email to the same household again
    just updates the name; someone already in a household, or invited to
    another, can't be."""
    email, name = email.strip(), name.strip()
    if not EMAIL.match(email) or len(email) > EMAIL_LENGTH:
        raise Invalid(f"{email or 'That'} isn't an email address.")
    if not name or len(name) > NAME_LENGTH:
        raise Invalid(f"Give them a name (up to {NAME_LENGTH} characters).")
    user_id = _auth_user_id(session, email)
    membership = session.get(AppUser, user_id) if user_id is not None else None
    if membership is not None:
        session.rollback()
        if membership.household_id == household.id:
            raise Refused(f"{email} is already in this household.")
        raise Refused(f"{email} is in another household.")
    existing = _invite_for(session, email)
    if existing is not None and existing.household_id != household.id:
        session.rollback()
        raise Refused(f"{email} is invited to another household.")
    invited = existing or HouseholdInvite(
        household_id=household.id, email=email, name=name, invited_by=invited_by
    )
    invited.name = name
    session.add(invited)
    session.commit()
    session.refresh(invited)
    log.info(
        "household.invited",
        extra={
            "household_id": str(household.id),
            "invite_id": str(invited.id),
            "again": bool(existing),
        },
    )
    return invited


def invites_of(session: Session, household_id: UUID) -> list[HouseholdInvite]:
    """The household's invitations not used yet, oldest first."""
    return list(
        session.exec(
            select(HouseholdInvite)
            .where(HouseholdInvite.household_id == household_id)
            .order_by(col(HouseholdInvite.created_at), col(HouseholdInvite.id))
        ).all()
    )


def cancel_invite(session: Session, household_id: UUID, invite_id: UUID) -> None:
    """Withdraw an invitation (one already used or withdrawn: nothing to do)."""
    session.execute(
        delete(HouseholdInvite).where(
            col(HouseholdInvite.id) == invite_id, col(HouseholdInvite.household_id) == household_id
        )
    )
    session.commit()
    log.info("household.invite_cancelled", extra={"invite_id": str(invite_id)})


def may_sign_up(session: Session, email: str) -> bool:
    """Whether a new account may be made for this email: it has an invitation."""
    return _invite_for(session, email) is not None


def join_if_invited(session: Session, user_id: UUID) -> Household | None:
    """The account's household: joined now, if it isn't in one and its email has
    an invitation (which is used up); None if it's in none and isn't invited."""
    member = session.get(AppUser, user_id)
    if member is not None:
        return session.get_one(Household, member.household_id)
    email = _account_email(session, user_id)
    # Locked: an invitation withdrawn meanwhile either is used now, or isn't there.
    invited = _invite_for(session, email, lock=True) if email is not None else None
    if invited is None:
        session.rollback()
        # Another request may have just used it, joining them.
        member = session.get(AppUser, user_id)
        return session.get_one(Household, member.household_id) if member is not None else None
    household = session.get_one(Household, invited.household_id)
    # In the order they were invited, not the order they first signed in.
    session.add(AppUser(user_id=user_id, household_id=household.id, invited_at=invited.created_at))
    # Signing in with a code leaves the account unnamed: it's called what the
    # invitation said (the account's own name, once it has one, stays).
    session.execute(
        text(
            'UPDATE neon_auth."user" SET name = :name '
            "WHERE id = :id AND btrim(coalesce(name, '')) = ''"
        ),
        {"name": invited.name, "id": user_id},
    )
    session.delete(invited)
    session.commit()
    log.info("household.joined", extra={"household_id": str(household.id), "user_id": str(user_id)})
    return household


def remove_member(session: Session, household_id: UUID, user_id: UUID) -> None:
    """End someone's membership (anyone in the household can, themselves too):
    they lose access at once, their phones and reminder schedule go, and an order
    lock of theirs is released. The account stays. The last member can't be removed."""
    # The household's row first, so two removals at once can't empty it.
    session.exec(for_update(select(Household).where(Household.id == household_id))).one()
    member = session.get(AppUser, user_id)
    if member is None or member.household_id != household_id:
        session.rollback()
        raise NotFound("They aren't in this household.")
    count = session.exec(
        select(func.count()).select_from(AppUser).where(AppUser.household_id == household_id)
    ).one()
    if count == 1:
        session.rollback()
        raise Refused("They're the household's only member: a household can't be left empty.")
    orders.release_locks_of(session, household_id, user_id)
    session.delete(member)  # its push devices go with it
    session.commit()
    log.info(
        "household.removed", extra={"household_id": str(household_id), "user_id": str(user_id)}
    )
    try:
        reminders.get_scheduler().delete(user_id)
    except reminders.ScheduleError as error:
        # The schedule finds no member when it fires, and deletes itself then.
        log.error(
            "household.schedule_not_deleted", extra={"user_id": str(user_id), "error": str(error)}
        )


def member_id(session: Session, email: str) -> UUID:
    """The id of the household member with this email."""
    user_id = _auth_user_id(session, email)
    if user_id is None or session.get(AppUser, user_id) is None:
        raise HouseholdError(f"{email} isn't in a household.")
    return user_id


def invite_by_name(session: Session, household_name: str, email: str, name: str) -> HouseholdInvite:
    """The admin command's invite: by the household's name, invited by no member."""
    return invite(session, get(session, household_name), email, name, invited_by=None)


def remove(session: Session, email: str) -> None:
    """The admin command's remove: end their membership, by email (the account stays)."""
    user_id = _auth_user_id(session, email)
    member = session.get(AppUser, user_id) if user_id is not None else None
    if user_id is None or member is None:
        raise NotFound(f"{email} isn't in a household.")
    remove_member(session, member.household_id, user_id)


@dataclass(frozen=True)
class Person:
    """Someone in a household, as Neon Auth knows them."""

    user_id: UUID
    email: str | None
    name: str | None


def members_of(session: Session, household_id: UUID) -> list[Person]:
    """Everyone in the household, in the order they were invited."""
    rows = session.execute(
        text(
            "SELECT m.user_id, u.email, u.name FROM app_user AS m "
            'JOIN neon_auth."user" AS u ON u.id = m.user_id '
            "WHERE m.household_id = :household_id ORDER BY m.invited_at, m.user_id"
        ),
        {"household_id": household_id},
    ).all()
    return [Person(user_id=row.user_id, email=row.email, name=row.name or None) for row in rows]


def members(session: Session, household_name: str) -> list[Person]:
    """Everyone in the named household, in the order they were invited."""
    return members_of(session, get(session, household_name).id)
