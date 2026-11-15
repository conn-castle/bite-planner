"""Historical receipt recovery through the admin CLI and the app's order API."""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from bite_planner_agent.checkout import ClickRequest
from bite_planner_agent.windows import Window
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, col, select

from bite_planner import booking
from bite_planner.checkout import Booking, BookIssue
from bite_planner.jobs import claim_job, finish_job
from bite_planner.models import (
    BookState,
    DeliveryDecision,
    DeliveryDecisionKind,
    GroceryOrder,
    Job,
    JobKind,
)
from tests.test_api import CASTLE, get_order, placed_order, tomorrow
from tests.test_api import client as client
from tests.test_api import dispatcher as dispatcher
from tests.test_api import households as households
from tests.test_failures import TASK

NUMBER = "TEST-1234567890"
AUTHORIZED_CENTS = 11302
WINDOW = Window(start=time(16), end=time(18))


@dataclass(frozen=True)
class Purchase:
    order_id: UUID
    job_id: UUID
    receipt: Path


@pytest.fixture
def purchase(client: TestClient, session: Session, tmp_path: Path) -> Purchase:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=4))
    order = session.get_one(GroceryOrder, UUID(placed["id"]))
    job_id = order.book_job_id
    assert job_id is not None
    assert claim_job(session, job_id, TASK) is not None
    assert order.delivery_date is not None and order.delivery_by is not None
    assert booking.claim_click(
        session,
        job_id,
        ClickRequest(
            day=order.delivery_date,
            by=order.delivery_by,
            window=WINDOW,
            total_cents=AUTHORIZED_CENTS,
        ),
    )
    booking.finish(
        session, job_id, BookState.CHECK_INSTACART, issue=BookIssue(message="Unconfirmed purchase.")
    )
    assert finish_job(session, job_id)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "attempt_job_id": str(job_id),
                "delivery_date": tomorrow().isoformat(),
                "order_number": NUMBER,
                "window": {"start": "16:00:00", "end": "18:00:00"},
                "authorized_total_cents": AUTHORIZED_CENTS,
            }
        )
    )
    return Purchase(order.id, job_id, receipt)


def run(
    purchase: Purchase, *options: str, household: str = "Conn Castle"
) -> subprocess.CompletedProcess[str]:
    # A real CLI process isolates its logging setup from the HTTP client.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from unittest.mock import patch\n"
            "from bite_planner.cli import app\n"
            "with patch('bite_planner.push.notify', "
            "side_effect=SystemExit(86)):\n"
            "    app()\n",
            "recover-booking",
            str(purchase.order_id),
            "--household",
            household,
            "--receipt",
            str(purchase.receipt),
            *options,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def snapshot(session: Session) -> dict[str, list[str]]:
    """Actual rows, including audit history and all ordering inputs."""
    return {
        table: list(
            session.execute(
                text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY to_jsonb(t)::text')
            ).scalars()
        )
        for table in (
            "grocery_order",
            "job",
            "delivery_decision",
            "grocery_order_meal",
            "shopping_list_item",
        )
    }


def replace_receipt(purchase: Purchase, field: str, value: object) -> None:
    receipt: dict[str, object] = json.loads(purchase.receipt.read_text())
    receipt[field] = value
    purchase.receipt.write_text(json.dumps(receipt))


def test_recovery_restores_the_apps_receipt_and_retains_purchase_authority_and_inputs(
    purchase: Purchase, client: TestClient, session: Session
) -> None:
    before = snapshot(session)
    original = session.get_one(GroceryOrder, purchase.order_id).model_dump()

    result = run(purchase)

    assert result.returncode == 0, result.stderr
    assert "Restored" in result.stdout
    shown = get_order(client, str(purchase.order_id))
    assert shown["book_state"] == "booked"
    assert shown["book_issue"] is None
    assert shown["booking"] == {
        "order_number": NUMBER,
        "window": {"start": "16:00:00", "end": "18:00:00"},
        "total_cents": AUTHORIZED_CENTS,
        "swaps": [],
    }
    after = snapshot(session)
    for table in ("job", "grocery_order_meal", "shopping_list_item"):
        assert after[table] == before[table]
    session.expire_all()
    recovered = session.get_one(GroceryOrder, purchase.order_id).model_dump()
    for field in original.keys() - {"book_state", "booking", "book_issue", "updated_at"}:
        assert recovered[field] == original[field]
    decisions = session.exec(
        select(DeliveryDecision)
        .where(DeliveryDecision.grocery_order_id == purchase.order_id)
        .order_by(col(DeliveryDecision.occurred_at))
    ).all()
    confirmed, booked = decisions[-2:]
    assert len(after["delivery_decision"]) == len(before["delivery_decision"]) + 2
    assert (confirmed.kind, confirmed.user_id, confirmed.job_id) == (
        DeliveryDecisionKind.CONFIRMED_PRESENT,
        None,
        purchase.job_id,
    )
    assert (booked.kind, booked.job_id) == (DeliveryDecisionKind.BOOKED, purchase.job_id)
    assert booked.booking is not None and booked.booking.model_dump(mode="json") == shown["booking"]


def test_check_validates_without_writing_and_recovery_is_idempotent(
    purchase: Purchase, session: Session
) -> None:
    before = snapshot(session)
    checked = run(purchase, "--check")
    assert checked.returncode == 0 and "no records changed" in checked.stdout
    assert snapshot(session) == before
    assert run(purchase).returncode == 0
    after = snapshot(session)
    for options in ((), ("--check",)):
        repeated = run(purchase, *options)
        assert repeated.returncode == 0 and "Already recovered" in repeated.stdout
        assert snapshot(session) == after
    replace_receipt(purchase, "order_number", "OTHER-1234567890")
    refused = run(purchase)
    assert refused.returncode == 1 and "different or incomplete receipt" in refused.stderr
    assert snapshot(session) == after
    # Matching fields never excuse a receipt naming another attempt.
    replace_receipt(purchase, "order_number", NUMBER)
    replace_receipt(purchase, "attempt_job_id", str(uuid4()))
    assert run(purchase).returncode == 1
    assert snapshot(session) == after


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt_job_id", "00000000-0000-4000-8000-000000000001", "purchase attempt"),
        ("delivery_date", "2000-01-01", "delivery date"),
        ("window", {"start": "14:00:00", "end": "16:00:00"}, "purchase authority"),
        # Instacart's later re-estimate is not the authorized checkout amount.
        ("authorized_total_cents", 12104, "purchase authority"),
        ("authorized_total_cents", True, "valid integer"),
        ("order_number", "Dry run", "pattern"),
        ("unexpected", "ignored?", "Extra inputs"),
    ],
)
def test_wrong_or_invalid_receipts_leave_every_record_unchanged(
    purchase: Purchase, session: Session, field: str, value: object, message: str
) -> None:
    replace_receipt(purchase, field, value)
    before = snapshot(session)
    for options in ((), ("--check",)):
        result = run(purchase, *options)
        assert result.returncode == 1 and message in result.stderr
        assert snapshot(session) == before


def test_wrong_household_and_missing_order_are_refused(
    purchase: Purchase, session: Session
) -> None:
    before = snapshot(session)
    wrong_house = run(purchase, household="Test Household")
    assert wrong_house.returncode == 1 and "No such order" in wrong_house.stderr
    nonexistent = Purchase(uuid4(), purchase.job_id, purchase.receipt)
    assert run(nonexistent).returncode == 1
    assert run(purchase, household="Missing Household").returncode == 1
    assert snapshot(session) == before


@pytest.mark.parametrize("problem", ["absent", "ambiguous", "different_date", "different_by"])
def test_missing_or_inconsistent_purchase_authority_is_refused(
    purchase: Purchase, session: Session, problem: str
) -> None:
    authority = session.exec(
        select(DeliveryDecision).where(
            DeliveryDecision.grocery_order_id == purchase.order_id,
            DeliveryDecision.kind == DeliveryDecisionKind.AUTHORIZED,
        )
    ).one()
    if problem == "absent":
        session.delete(authority)
    elif problem == "ambiguous":
        session.add(DeliveryDecision.model_validate(authority.model_dump(exclude={"id"})))
    else:
        if problem == "different_date":
            authority.delivery_date = tomorrow() - timedelta(days=1)
        else:
            authority.delivery_by = time(17)
        session.add(authority)
    session.commit()
    before = snapshot(session)
    result = run(purchase)
    assert result.returncode == 1
    assert "purchase authorization" in result.stderr or "purchase authority" in result.stderr
    assert snapshot(session) == before


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("member_confirmed", "different or incomplete receipt"),
        ("waiting", "Only an unconfirmed purchase"),
        ("missing_click", "authorized purchase attempt"),
        ("wrong_job", "not this order's booking job"),
        ("wrong_order_job", "not this order's booking job"),
    ],
)
def test_recovery_refuses_other_states_and_invalid_attempts(
    purchase: Purchase, session: Session, state: str, message: str
) -> None:
    order = session.get_one(GroceryOrder, purchase.order_id)
    if state == "member_confirmed":
        assert order.placed_by is not None
        booking.settle(session, CASTLE, order.id, True, order.placed_by)
    elif state == "waiting":
        order.book_state = BookState.WAITING
        session.add(order)
    elif state == "missing_click":
        order.clicked_at = None
        session.add(order)
    else:
        if state == "wrong_job":
            assert order.quote_id is not None
            wrong_attempt = order.quote_id
        else:
            other = GroceryOrder(household_id=CASTLE, created_by=order.created_by)
            session.add(other)
            session.flush()
            wrong_attempt = uuid4()
            session.add(
                Job(
                    id=wrong_attempt,
                    grocery_order_id=other.id,
                    kind=JobKind.BOOK,
                    requested_by=order.created_by,
                )
            )
            session.flush()
        order.click_job_id = wrong_attempt
        replace_receipt(purchase, "attempt_job_id", str(wrong_attempt))
        session.add(order)
        # Matching authority cannot excuse a non-book or another order's job.
        authority = session.exec(
            select(DeliveryDecision).where(
                DeliveryDecision.grocery_order_id == purchase.order_id,
                DeliveryDecision.kind == DeliveryDecisionKind.AUTHORIZED,
            )
        ).one()
        authority.job_id = wrong_attempt
        session.add(authority)
    session.commit()
    before = snapshot(session)
    result = run(purchase)
    assert result.returncode == 1 and message in result.stderr
    assert snapshot(session) == before


def test_receipt_already_attached_to_another_order_is_refused(
    purchase: Purchase, session: Session
) -> None:
    other = GroceryOrder(household_id=CASTLE, created_by=uuid4())
    # The duplicate check covers the receipt itself, regardless of the other
    # record's status; it never moves that receipt to a different order.
    other.booking = Booking(order_number=NUMBER, window=WINDOW, total_cents=AUTHORIZED_CENTS)
    session.add(other)
    session.commit()
    before = snapshot(session)
    result = run(purchase)
    assert result.returncode == 1 and "already associated" in result.stderr
    assert snapshot(session) == before


@pytest.mark.parametrize("problem", ["missing", "malformed", "invalid_utf8"])
def test_unreadable_or_malformed_receipt_fails_without_writing(
    purchase: Purchase, session: Session, problem: str
) -> None:
    if problem == "missing":
        purchase.receipt.unlink()
    elif problem == "malformed":
        purchase.receipt.write_text("{bad json")
    else:
        purchase.receipt.write_bytes(b"\xff")
    before = snapshot(session)
    assert run(purchase).returncode == 1
    assert snapshot(session) == before
