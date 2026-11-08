"""Delivery approval through HTTP, including stale choices and uncertain purchases."""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from castle_chef_agent.checkout import ClickRequest
from castle_chef_agent.windows import Availability, AvailableDay, DeliveryProposal, Window
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from castle_chef import booking, orders, worker
from castle_chef.checkout import BookIssue, CheckoutError, Quote
from castle_chef.jobs import claim_job, finish_job
from castle_chef.models import (
    BookState,
    DeliveryDecision,
    DeliveryDecisionKind,
    GroceryOrder,
    Household,
    Job,
)
from castle_chef.push import NotificationKind
from tests.test_api import (
    CHECKOUT,
    DAD,
    MOM,
    NEIGHBOR,
    ZONE,
    RecordingDispatcher,
    as_user,
    change,
    detail,
    finish_quote,
    get_order,
    ok,
    open_draft,
    pick,
    place,
    placed_order,
    put_dates,
    ready_list,
    ready_quote,
    request_quote,
    run_at_once,
    shopped_draft,
    tomorrow,
    with_suggestions,
)
from tests.test_api import client as client
from tests.test_api import dispatcher as dispatcher
from tests.test_api import households as households
from tests.test_failures import TASK, task_stopped
from tests.test_failures import events as events
from tests.test_notifications import RecordingSender, register
from tests.test_notifications import sender as sender


def time_proposal(client: TestClient, session: Session) -> tuple[str, DeliveryProposal]:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=4))
    order_id = str(placed["id"])
    order = session.get_one(GroceryOrder, UUID(order_id))
    job_id = order.book_job_id
    assert job_id is not None
    assert claim_job(session, job_id, TASK) is not None
    proposed = tomorrow() + timedelta(days=1)
    start = datetime.combine(proposed, time(12), ZoneInfo(ZONE))
    now = datetime.now(UTC)
    proposal = DeliveryProposal(
        day=proposed,
        window=Window(start=time(12), end=time(14)),
        observed_at=now,
        starts_at=start,
        valid_until=now + timedelta(minutes=30),
    )
    booking.finish(
        session,
        job_id,
        BookState.NEEDS_TIME,
        issue=BookIssue(message="The approved delivery is unavailable.", proposal=proposal),
    )
    assert finish_job(session, job_id)
    return order_id, proposal


def test_same_day_approval_uses_the_store_clock_and_starts_booking_immediately(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today = booking.store_now().date()
    now = datetime.combine(today, time(9), ZoneInfo(ZONE))
    monkeypatch.setattr(orders, "delivery_now", lambda: now)
    order_id, [meal, _] = with_suggestions(client)
    ok(pick(client, order_id, meal))
    ok(put_dates(client, order_id, today, today + timedelta(days=3), by="16:00"))
    ready_list(client, order_id)
    quote_id = ready_quote(client, order_id)
    quoted = session.get_one(Job, UUID(quote_id))
    assert quoted.quote is not None and quoted.quote.availability is not None
    quoted.quote = quoted.quote.model_copy(
        update={"availability": quoted.quote.availability.model_copy(update={"observed_at": now})}
    )
    session.add(quoted)
    session.commit()
    result = ok(
        client.post(
            f"/v1/grocery-orders/{order_id}/place",
            headers=as_user(MOM, "Pacific/Kiritimati"),
            json={
                "delivery_date": today.isoformat(),
                "delivery_by": "16:00",
                "next_delivery_date": (today + timedelta(days=3)).isoformat(),
                "quote_id": quote_id,
            },
        )
    )
    assert result["delivery_date"] == today.isoformat()
    assert result["book_state"] == "booking"


def test_a_passed_same_day_deadline_cannot_be_saved(client: TestClient) -> None:
    from tests.test_api import open_draft

    now = booking.store_now()
    before = now - timedelta(minutes=1)
    order_id = str(open_draft(client)["id"])
    detail(
        put_dates(
            client,
            order_id,
            before.date(),
            before.date() + timedelta(days=2),
            by=before.strftime("%H:%M"),
        ),
        422,
    )
    assert get_order(client, order_id)["delivery_date"] is None


@pytest.mark.parametrize("hours,status", [(2, 200), (11, 200), (12, 201), (13, 201)])
def test_delivery_choices_are_reused_for_twelve_hours(
    client: TestClient,
    session: Session,
    hours: int,
    status: int,
) -> None:
    order_id, _ = shopped_draft(client)
    first = ok(request_quote(client, order_id), 201)
    finish_quote(first["id"])
    job = session.get_one(Job, UUID(first["id"]))
    assert job.quote is not None and job.quote.availability is not None
    observed = datetime.now(UTC) - timedelta(hours=hours)
    job.quote = job.quote.model_copy(
        update={"availability": job.quote.availability.model_copy(update={"observed_at": observed})}
    )
    session.add(job)
    session.commit()
    shown = get_order(client, order_id)["quote"]
    assert shown["availability_current"] is (status == 200)
    assert datetime.fromisoformat(shown["availability_valid_until"]) == observed + timedelta(
        hours=12
    )
    again = ok(request_quote(client, order_id), status)
    assert (again["id"] == first["id"]) is (status == 200)


def test_an_hours_old_delivery_choice_can_be_approved_and_booked(
    client: TestClient,
    session: Session,
) -> None:
    order_id, _ = shopped_draft(client)
    delivery = tomorrow()
    ok(put_dates(client, order_id, delivery, delivery + timedelta(days=3), by="18:00"))
    quote_id = ready_quote(client, order_id)
    job = session.get_one(Job, UUID(quote_id))
    assert job.quote is not None and job.quote.availability is not None
    job.quote = job.quote.model_copy(
        update={
            "availability": job.quote.availability.model_copy(
                update={"observed_at": datetime.now(UTC) - timedelta(hours=2)}
            )
        }
    )
    session.add(job)
    session.commit()
    approved = ok(place(client, order_id))
    assert approved["book_state"] == "booking"


def test_a_stopped_quote_notifies_the_waiting_member(
    client: TestClient,
    session: Session,
    events: TestClient,
    sender: RecordingSender,
) -> None:
    order_id, _ = shopped_draft(client)
    token = "ab" * 32
    assert register(client, token).status_code == 204
    job = ok(request_quote(client, order_id), 201)
    ok(events.post("/events", json=task_stopped(job["id"], 1)))
    [(phone, notification)] = sender.sent
    assert phone == token
    assert notification.kind == NotificationKind.QUOTE_FAILED
    assert notification.order_id == UUID(order_id)


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_a_delivery_check_notifies_its_requester_when_the_worker_finishes(
    client: TestClient,
    sender: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed: bool,
    changed: bool,
    session: Session,
) -> None:
    order_id, _ = shopped_draft(client)
    token = "ab" * 32
    assert register(client, token).status_code == 204
    job = ok(request_quote(client, order_id), 201)

    def check(job: Job, job_dir: Path) -> Quote:
        if changed:
            order = session.get_one(GroceryOrder, job.grocery_order_id)
            household = session.get_one(Household, order.household_id)
            household.tip_percent += 1
            session.add(household)
            session.commit()
        if failed:
            raise CheckoutError("Instacart is unavailable.")
        return Quote(
            checkout=CHECKOUT,
            availability=Availability(
                observed_at=datetime.now(UTC),
                time_zone=ZONE,
                days=[AvailableDay(day=tomorrow(), windows=[Window(start=time(16), end=time(18))])],
            ),
        )

    monkeypatch.setattr(worker, "process_quote", check)
    monkeypatch.setenv("CASTLE_CHEF_JOBS_DIR", str(tmp_path / "jobs"))
    from castle_chef.settings import get_settings

    get_settings.cache_clear()
    worker.run_job(UUID(job["id"]))
    if changed:
        assert sender.sent == []
        return
    [(phone, notification)] = sender.sent
    assert phone == token
    assert notification.order_id == UUID(order_id)
    assert notification.kind == (
        NotificationKind.QUOTE_FAILED if failed else NotificationKind.QUOTE_READY
    )


def test_simultaneous_approval_of_one_proposal_starts_one_purchase_job(
    client: TestClient,
    session: Session,
    dispatcher: RecordingDispatcher,
) -> None:
    order_id, proposal = time_proposal(client, session)
    before = len(dispatcher.dispatched)
    body = {
        "proposal_id": str(proposal.id),
        "next_delivery_date": (proposal.day + timedelta(days=3)).isoformat(),
    }
    responses = run_at_once(
        [
            lambda: client.post(
                f"/v1/grocery-orders/{order_id}/delivery/approve", headers=as_user(MOM), json=body
            ),
            lambda: client.post(
                f"/v1/grocery-orders/{order_id}/delivery/approve", headers=as_user(MOM), json=body
            ),
        ]
    )
    assert [response.status_code for response in responses] == [200, 200]
    assert len(dispatcher.dispatched) == before + 1
    shown = get_order(client, order_id)
    assert (shown["delivery_date"], shown["delivery_by"], shown["book_state"]) == (
        proposal.day.isoformat(),
        "14:00:00",
        "booking",
    )
    [decision] = session.exec(
        select(DeliveryDecision).where(
            DeliveryDecision.grocery_order_id == UUID(order_id),
            DeliveryDecision.kind == DeliveryDecisionKind.REAPPROVED,
        )
    ).all()
    assert (
        decision.user_id,
        decision.proposal_id,
        decision.delivery_date,
        decision.delivery_by,
    ) == (
        MOM,
        proposal.id,
        proposal.day,
        time(14),
    )


def test_a_proposal_from_a_previous_approval_cannot_succeed_after_reopening_and_replacing(
    client: TestClient, session: Session, dispatcher: RecordingDispatcher
) -> None:
    order_id, proposal = time_proposal(client, session)
    body = {
        "proposal_id": str(proposal.id),
        "next_delivery_date": (proposal.day + timedelta(days=3)).isoformat(),
    }
    approved = ok(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/approve", headers=as_user(MOM), json=body
        )
    )
    order = session.get_one(GroceryOrder, UUID(order_id))
    session.refresh(order)
    job_id = order.book_job_id
    assert job_id is not None
    assert claim_job(session, job_id, TASK) is not None
    booking.finish(session, job_id, BookState.NOT_BOOKED, issue=BookIssue(message="Not ordered."))
    assert finish_job(session, job_id)
    ok(change(client, order_id))
    assert open_draft(client)["id"] == order_id
    ok(put_dates(client, order_id, proposal.day, proposal.day + timedelta(days=4), by="18:00"))
    assert ok(place(client, order_id))["next_delivery_date"] != approved["next_delivery_date"]
    dispatched = len(dispatcher.dispatched)
    detail(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/approve", headers=as_user(MOM), json=body
        ),
        409,
    )
    assert len(dispatcher.dispatched) == dispatched


def test_decide_later_does_not_change_dates_or_start_booking(
    client: TestClient,
    session: Session,
    dispatcher: RecordingDispatcher,
) -> None:
    order_id, proposal = time_proposal(client, session)
    before = get_order(client, order_id)
    queued = list(dispatcher.dispatched)
    body = {"proposal_id": str(proposal.id)}
    deferred = ok(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/later", headers=as_user(MOM), json=body
        )
    )
    repeated = ok(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/later", headers=as_user(MOM), json=body
        )
    )
    assert deferred["delivery_date"] == before["delivery_date"]
    assert deferred["book_state"] == "needs_time"
    assert deferred["book_issue"]["deferred_at"] is not None
    assert repeated["book_issue"]["deferred_at"] == deferred["book_issue"]["deferred_at"]
    assert dispatcher.dispatched == queued


def test_delivery_evidence_keeps_the_original_approval_and_survives_cancellation(
    client: TestClient,
    session: Session,
) -> None:
    order_id, proposal = time_proposal(client, session)
    decisions = list(
        session.exec(
            select(DeliveryDecision).where(
                DeliveryDecision.grocery_order_id == UUID(order_id),
            )
        ).all()
    )
    [approved] = [item for item in decisions if item.kind == DeliveryDecisionKind.APPROVED]
    [proposed] = [item for item in decisions if item.kind == DeliveryDecisionKind.PROPOSED]
    assert approved.user_id == MOM and approved.delivery_date == tomorrow()
    assert approved.delivery_by == time(18)
    assert proposed.delivery_date == approved.delivery_date
    assert proposed.issue is not None and proposed.issue.proposal == proposal
    assert proposed.proposal_id == proposal.id
    assert client.delete(f"/v1/grocery-orders/{order_id}", headers=as_user(MOM)).status_code == 204
    session.expire_all()
    assert session.get(GroceryOrder, UUID(order_id)) is None
    assert {
        item.id
        for item in session.exec(
            select(DeliveryDecision).where(
                DeliveryDecision.grocery_order_id == UUID(order_id),
            )
        ).all()
    } == {item.id for item in decisions}


def test_an_unidentified_local_worker_cannot_receive_live_purchase_authority(
    client: TestClient,
    session: Session,
) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    order = session.get_one(GroceryOrder, UUID(placed["id"]))
    job_id = order.book_job_id
    assert job_id is not None and claim_job(session, job_id) is not None
    assert not booking.claim_click(
        session,
        job_id,
        ClickRequest(
            day=tomorrow(),
            by=time(18),
            window=Window(start=time(16), end=time(18)),
            total_cents=2768,
        ),
    )
    session.refresh(order)
    assert order.clicked_at is None and order.click_job_id is None


@pytest.mark.parametrize("failure", ["wrong_id", "old_snapshot", "started", "invalid_next"])
def test_stale_or_invalid_reapproval_cannot_start_a_purchase(
    client: TestClient,
    session: Session,
    dispatcher: RecordingDispatcher,
    failure: str,
) -> None:
    order_id, proposal = time_proposal(client, session)
    now = booking.store_now()
    if failure in ("old_snapshot", "started"):
        order = session.get_one(GroceryOrder, UUID(order_id))
        assert order.book_issue is not None
        update = (
            {"observed_at": now - timedelta(hours=1)}
            if failure == "old_snapshot"
            else {"day": now.date(), "window": Window(start=time(0), end=time(1))}
        )
        order.book_issue = order.book_issue.model_copy(
            update={"proposal": proposal.model_copy(update=update)}
        )
        session.add(order)
        session.commit()
    queued = list(dispatcher.dispatched)
    body = {
        "proposal_id": str(uuid4() if failure == "wrong_id" else proposal.id),
        "next_delivery_date": (
            proposal.day if failure == "invalid_next" else proposal.day + timedelta(days=3)
        ).isoformat(),
    }
    detail(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/approve", headers=as_user(MOM), json=body
        ),
        409,
    )
    assert dispatcher.dispatched == queued
    assert get_order(client, order_id)["book_state"] == "needs_time"


def test_another_household_cannot_approve_a_delivery(client: TestClient, session: Session) -> None:
    order_id, proposal = time_proposal(client, session)
    detail(
        client.post(
            f"/v1/grocery-orders/{order_id}/delivery/approve",
            headers=as_user(NEIGHBOR),
            json={
                "proposal_id": str(proposal.id),
                "next_delivery_date": (proposal.day + timedelta(days=2)).isoformat(),
            },
        ),
        404,
    )


def test_persisted_purchase_authorization_overrides_a_local_no_click_result(
    client: TestClient,
    session: Session,
) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    order = session.get_one(GroceryOrder, UUID(placed["id"]))
    job_id = order.book_job_id
    assert job_id is not None
    assert claim_job(session, job_id, TASK) is not None
    assert booking.claim_click(
        session,
        job_id,
        ClickRequest(
            day=tomorrow(),
            by=time(18),
            window=Window(start=time(16), end=time(18)),
            total_cents=2768,
        ),
    )
    # GO may have committed without reaching the browser. The local result is not proof of failure.
    now = datetime.now(UTC)
    candidate = DeliveryProposal(
        day=tomorrow(),
        window=Window(start=time(18), end=time(20)),
        observed_at=now,
        starts_at=datetime.combine(tomorrow(), time(18), ZoneInfo(ZONE)),
        valid_until=now + timedelta(minutes=30),
    )
    booking.finish(
        session,
        job_id,
        BookState.NOT_BOOKED,
        issue=BookIssue(message="The browser didn't receive GO.", proposal=candidate),
    )
    shown = get_order(client, str(order.id))
    assert shown["book_state"] == "check_instacart"
    assert shown["book_issue"]["proposal"] is None
    assert "Nothing was ordered" not in shown["book_issue"]["message"]
    for path in ("book", "change", "delivery/approve"):
        body = (
            {
                "proposal_id": str(uuid4()),
                "next_delivery_date": (tomorrow() + timedelta(days=4)).isoformat(),
            }
            if path == "delivery/approve"
            else None
        )
        detail(
            client.post(f"/v1/grocery-orders/{order.id}/{path}", headers=as_user(MOM), json=body),
            409,
        )


def test_negative_resolution_needs_the_exact_tasks_stop_and_records_human_verification(
    client: TestClient,
    events: TestClient,
    session: Session,
) -> None:
    placed = placed_order(client, tomorrow(), tomorrow() + timedelta(days=3))
    order_id = str(placed["id"])
    order = session.get_one(GroceryOrder, UUID(order_id))
    job_id = order.book_job_id
    assert job_id is not None
    assert claim_job(session, job_id, TASK) is not None
    assert booking.claim_click(
        session,
        job_id,
        ClickRequest(
            day=tomorrow(),
            by=time(18),
            window=Window(start=time(16), end=time(18)),
            total_cents=2768,
        ),
    )
    booking.finish(
        session,
        job_id,
        BookState.CHECK_INSTACART,
        issue=BookIssue(message="Purchase outcome unknown."),
    )
    assert finish_job(session, job_id)
    path = f"/v1/grocery-orders/{order_id}/settle"
    assert get_order(client, order_id)["can_confirm_absent"] is False
    detail(client.post(path, headers=as_user(MOM), json={"placed": False}), 409)
    # A duplicate task can have stopped while the purchasing task remains alive.
    ok(events.post("/events", json=task_stopped(str(job_id), 0, task=TASK + "-duplicate")))
    detail(client.post(path, headers=as_user(MOM), json={"placed": False}), 409)
    # Successful exit is also closure proof; no timer or failure inference.
    ok(events.post("/events", json=task_stopped(str(job_id), 0)))
    assert get_order(client, order_id)["can_confirm_absent"] is True
    settled = ok(client.post(path, headers=as_user(DAD), json={"placed": False}))
    assert settled["book_state"] == "not_booked"
    session.expire_all()
    current = session.get_one(GroceryOrder, UUID(order_id))
    assert current.clicked_at is None and current.click_job_id is None
    proof = session.exec(
        select(DeliveryDecision).where(
            DeliveryDecision.grocery_order_id == current.id,
            DeliveryDecision.kind == DeliveryDecisionKind.CONFIRMED_ABSENT,
        )
    ).one()
    assert proof.user_id == DAD and proof.job_id == job_id
    assert proof.worker_stop is not None and proof.worker_stop.event.detail.task_arn == TASK
    ok(client.post(f"/v1/grocery-orders/{order_id}/book", headers=as_user(MOM)))
    assert not booking.claim_click(
        session,
        job_id,
        ClickRequest(
            day=tomorrow(),
            by=time(18),
            window=Window(start=time(16), end=time(18)),
            total_cents=2768,
        ),
    )
