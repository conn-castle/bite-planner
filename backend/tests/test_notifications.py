"""Notifications: phones registering through the API, members' reminder schedules,
the daily reminder a schedule's firing sends, and sending to APNs. AWS and APNs
are stand-ins (a recording scheduler and sender; botocore's Stubber and an HTTP
mock transport for the real clients)."""

# Fixtures imported from test_api are parameters here, which ruff reads as redefinitions.
# ruff: noqa: F811

import base64
import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx2
import jwt
import pytest
from botocore.stub import Stubber
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from castle_chef import push, reminders
from castle_chef.api import create_app
from castle_chef.models import AppUser, PushDevice
from castle_chef.push import ApnsSender, Notification, NotificationKind, Outcome, Sent
from castle_chef.reminders import AwsReminderScheduler, ReminderEvent, ScheduleError
from castle_chef.settings import get_settings
from tests.test_api import (  # noqa: F401  (fixtures)
    DAD,
    MOM,
    NEIGHBOR,
    STRANGER,
    ZONE,
    as_user,
    client,
    detail,
    dispatcher,
    households,
    next_order,
    no_content,
    ok,
    open_draft,
    patch_personal,
    placed_order,
    tomorrow,
)

PHONE = "ab" * 32  # an APNs device token: 32 bytes, hex
OTHER_PHONE = "cd" * 32


class RecordingScheduler:
    """Members' schedules as EventBridge Scheduler would hold them."""

    def __init__(self) -> None:
        self.schedules: dict[UUID, tuple[time, str]] = {}
        self.fail = False

    def upsert(self, user_id: UUID, at: time, zone: str) -> None:
        if self.fail:
            raise ScheduleError("Scheduler unavailable")
        self.schedules[user_id] = (at, zone)

    def delete(self, user_id: UUID) -> None:
        if self.fail:
            raise ScheduleError("Scheduler unavailable")
        self.schedules.pop(user_id, None)

    def user_ids(self) -> set[UUID]:
        return set(self.schedules)


class RecordingSender:
    """APNs: every notification sent, and how it answers for each phone."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Notification]] = []
        self.answers: dict[str, Sent] = {}

    def send(self, token: str, notification: Notification) -> Sent:
        self.sent.append((token, notification))
        return self.answers.get(token, Sent(Outcome.SENT))


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> RecordingScheduler:
    recording = RecordingScheduler()
    monkeypatch.setattr(reminders, "get_scheduler", lambda: recording)
    return recording


@pytest.fixture
def sender(monkeypatch: pytest.MonkeyPatch) -> RecordingSender:
    recording = RecordingSender()
    monkeypatch.setattr(push, "get_sender", lambda: recording)
    return recording


def register(client: TestClient, token: str, user: UUID = MOM) -> Any:
    return client.put(f"/v1/me/devices/{token}", headers=as_user(user))


def unregister(client: TestClient, token: str, user: UUID = MOM) -> Any:
    return client.delete(f"/v1/me/devices/{token}", headers=as_user(user))


def devices(session: Session) -> dict[str, UUID]:
    session.expire_all()
    return {device.token: device.user_id for device in session.exec(select(PushDevice)).all()}


def firing(day: date, at: time = time(8, 0), zone: str = ZONE, user: UUID = MOM) -> ReminderEvent:
    """What a member's schedule sends when it fires on `day` at `at` in `zone`."""
    due = datetime.combine(day, at, ZoneInfo(zone)).astimezone(UTC)
    return ReminderEvent(version=1, user_id=user, scheduled_time=due)


def reminded(sender: RecordingSender) -> list[tuple[str, str]]:
    return [(n.title, n.body) for _, n in sender.sent]


# Phones.


def test_a_phone_gets_the_notifications_of_whoever_signed_in_on_it_last(
    client: TestClient, session: Session
) -> None:
    no_content(register(client, PHONE))
    no_content(register(client, PHONE))  # reported again at each launch
    assert devices(session) == {PHONE: MOM}

    # Dad signs in on Mom's phone.
    no_content(register(client, PHONE.upper(), DAD))
    no_content(register(client, OTHER_PHONE, DAD))
    assert devices(session) == {PHONE: DAD, OTHER_PHONE: DAD}


def test_signing_out_removes_only_your_own_phone(client: TestClient, session: Session) -> None:
    no_content(register(client, PHONE))
    no_content(register(client, OTHER_PHONE, DAD))

    # Mom's old sign-out reaching the server after Dad took the phone leaves it his.
    no_content(unregister(client, OTHER_PHONE))
    no_content(unregister(client, PHONE))
    no_content(unregister(client, PHONE))  # again: nothing to remove

    assert devices(session) == {OTHER_PHONE: DAD}


@pytest.mark.parametrize("token", ["ab" * 31, "zz" * 32, "ab" * 101])
def test_a_token_that_is_not_an_apns_token_is_refused(
    client: TestClient, session: Session, token: str
) -> None:
    detail(register(client, token), 422)
    assert devices(session) == {}


def test_only_members_register_phones(client: TestClient, session: Session) -> None:
    detail(register(client, PHONE, STRANGER), 403)
    assert devices(session) == {}


# Schedules.


def test_a_members_schedule_follows_their_time_and_zone(
    client: TestClient, scheduler: RecordingScheduler
) -> None:
    # No zone yet: no schedule, even with a time.
    ok(patch_personal(client, {"notification_time": "07:30"}))
    assert scheduler.schedules == {}

    ok(patch_personal(client, {"time_zone": ZONE}))
    assert scheduler.schedules == {MOM: (time(7, 30), ZONE)}

    ok(patch_personal(client, {"notification_time": "18:45", "time_zone": "Europe/Paris"}))
    assert scheduler.schedules == {MOM: (time(18, 45), "Europe/Paris")}


def test_settings_that_change_nothing_leave_the_schedule_alone(
    client: TestClient, scheduler: RecordingScheduler
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}))
    scheduler.fail = True  # any call would fail the request

    ok(patch_personal(client, {"notification_time": "08:00", "time_zone": ZONE}))


def test_a_time_that_cannot_be_scheduled_is_not_saved(
    client: TestClient, scheduler: RecordingScheduler, caplog: pytest.LogCaptureFixture
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}))
    scheduler.fail = True

    message = detail(patch_personal(client, {"notification_time": "06:00"}), 503)

    assert message == "Couldn't update your reminder time. Try again."
    settings = ok(client.get("/v1/me", headers=as_user(MOM)))["settings"]
    assert settings == {"notification_time": "08:00:00", "time_zone": ZONE, "reminders_on": True}
    assert scheduler.schedules == {MOM: (time(8, 0), ZONE)}
    assert any(r.message == "settings.schedule_failed" for r in caplog.records)


def test_sync_rebuilds_every_schedule_from_the_database(
    client: TestClient, scheduler: RecordingScheduler, session: Session
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}))
    ok(patch_personal(client, {"time_zone": "Europe/Paris", "notification_time": "19:00"}, DAD))
    # Out of date: Mom's time changed without her schedule, a removed member's
    # schedule remains, and Neighbor has no zone.
    session.get_one(AppUser, MOM).notification_time = time(6, 15)
    session.commit()
    gone = uuid4()
    scheduler.schedules[gone] = (time(9, 0), ZONE)

    result = reminders.handle_event({"version": 1, "sync": True})

    assert result == {"upserted": 2, "deleted": 2, "failed": 0}
    assert scheduler.schedules == {
        MOM: (time(6, 15), ZONE),
        DAD: (time(19, 0), "Europe/Paris"),
    }
    assert NEIGHBOR not in scheduler.schedules


def test_sync_counts_the_schedules_it_could_not_save(
    client: TestClient, scheduler: RecordingScheduler
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}))
    scheduler.fail = True

    assert reminders.sync().model_dump() == {"upserted": 0, "deleted": 0, "failed": 3}


# Reminders.


@pytest.fixture
def due_order(client: TestClient, scheduler: RecordingScheduler) -> date:
    """Mom's household placed an order; its next one's target date is returned.
    Mom's phone is registered and her zone known."""
    ok(patch_personal(client, {"time_zone": ZONE}))
    no_content(register(client, PHONE))
    first = placed_order(client, tomorrow(), tomorrow() + timedelta(days=7))
    return date.fromisoformat(first["next_delivery_date"])


def test_reminders_start_the_lead_before_the_decide_by_date_and_go_on_until_placed(
    client: TestClient, due_order: date, sender: RecordingSender
) -> None:
    target = due_order
    decide_by = target - timedelta(days=1)  # the default offset
    start = decide_by - timedelta(days=2)  # the default lead
    delivery = f"{target:%A}, {target:%b} {target.day}"

    for day in [start - timedelta(days=1), start, decide_by, decide_by + timedelta(days=3)]:
        reminders.remind(firing(day))

    assert reminded(sender) == [
        ("Pick meals for Order 2", f"Decide by {decide_by:%A} for delivery {delivery}."),
        ("Last day to pick meals", f"Decide by today for delivery {delivery}."),
        (
            "Order 2 is overdue",
            f"It was due {decide_by:%A}. Pick meals for delivery as soon as you can.",
        ),
    ]
    [(token, first), *_] = sender.sent
    assert token == PHONE
    assert (first.kind, first.order_number, first.collapse_id) == (
        NotificationKind.REMINDER,
        2,
        "reminder",
    )


def test_a_member_who_turned_reminders_off_gets_none_and_the_others_still_do(
    client: TestClient, due_order: date, sender: RecordingSender, scheduler: RecordingScheduler
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}, DAD))
    no_content(register(client, OTHER_PHONE, DAD))
    ok(patch_personal(client, {"reminders_on": False}))
    day = due_order - timedelta(days=1)

    reminders.remind(firing(day))
    reminders.remind(firing(day, user=DAD))

    assert [token for token, _ in sender.sent] == [OTHER_PHONE]
    # Her schedule stays, and switched back on she gets the day's reminder.
    assert MOM in scheduler.schedules
    ok(patch_personal(client, {"reminders_on": True}))
    reminders.remind(firing(day))
    assert [token for token, _ in sender.sent] == [OTHER_PHONE, PHONE]


def test_a_draft_someone_is_working_on_still_gets_reminders(
    client: TestClient, due_order: date, sender: RecordingSender
) -> None:
    open_draft(client, DAD)

    reminders.remind(firing(due_order - timedelta(days=1)))

    assert len(sender.sent) == 1


def test_no_reminders_before_the_first_order_is_placed(
    client: TestClient, scheduler: RecordingScheduler, sender: RecordingSender
) -> None:
    ok(patch_personal(client, {"time_zone": ZONE}))
    no_content(register(client, PHONE))
    open_draft(client)

    reminders.remind(firing(tomorrow()))

    assert sender.sent == []


def test_placing_the_order_stops_its_reminders(
    client: TestClient, due_order: date, sender: RecordingSender
) -> None:
    # The next order is placed; the one after it isn't due for a while.
    placed_order(client, due_order, due_order + timedelta(days=10))

    reminders.remind(firing(due_order - timedelta(days=1)))

    assert sender.sent == []


def test_at_most_one_reminder_a_day(
    client: TestClient, due_order: date, sender: RecordingSender, session: Session
) -> None:
    day = due_order - timedelta(days=1)
    reminders.remind(firing(day))
    reminders.remind(firing(day))  # a retried firing
    reminders.remind(firing(day + timedelta(days=1)))

    assert len(sender.sent) == 2
    session.expire_all()
    assert session.get_one(AppUser, MOM).reminded_on == day + timedelta(days=1)


def test_the_reminders_day_is_the_members_own(
    client: TestClient, due_order: date, sender: RecordingSender
) -> None:
    decide_by = due_order - timedelta(days=1)
    # 1 AM in Kiritimati (UTC+14) on the decide-by day is still the day before in
    # UTC: it's the member's "last day" all the same.
    ok(patch_personal(client, {"time_zone": "Pacific/Kiritimati", "notification_time": "01:00"}))

    reminders.remind(firing(decide_by, time(1, 0), "Pacific/Kiritimati"))

    assert reminded(sender)[0][0] == "Last day to pick meals"


def test_a_time_repeated_when_the_clocks_go_back_sends_one_reminder(
    client: TestClient, due_order: date, sender: RecordingSender, caplog: pytest.LogCaptureFixture
) -> None:
    # 1:30 AM happens twice when New York's clocks go back on Nov 1 (the order is
    # overdue by then, so a reminder is due). Scheduler fires once; either instant
    # is the member's 1:30 on their Nov 1.
    ok(patch_personal(client, {"notification_time": "01:30"}))
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 1:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 1:30 EST

    for instant in (first, second):
        reminders.remind(ReminderEvent(version=1, user_id=MOM, scheduled_time=instant))

    assert reminded(sender)[0][0] == "Order 2 is overdue"
    assert len(sender.sent) == 1
    assert not any(r.message == "reminders.stale_schedule" for r in caplog.records)


def test_a_stale_schedule_sends_nothing_and_is_rebuilt(
    client: TestClient,
    due_order: date,
    sender: RecordingSender,
    scheduler: RecordingScheduler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scheduler.schedules[MOM] = (time(8, 5), ZONE)  # an update that never reached the database

    reminders.remind(firing(due_order - timedelta(days=1), time(8, 5)))

    assert sender.sent == []
    assert scheduler.schedules[MOM] == (time(8, 0), ZONE)
    assert any(r.message == "reminders.stale_schedule" for r in caplog.records)


def test_a_removed_members_schedule_deletes_itself(
    scheduler: RecordingScheduler, sender: RecordingSender
) -> None:
    gone = uuid4()
    scheduler.schedules[gone] = (time(8, 0), ZONE)

    reminders.remind(firing(tomorrow(), user=gone))

    assert scheduler.schedules == {}
    assert sender.sent == []


def test_every_phone_gets_the_reminder_and_dead_ones_are_forgotten(
    client: TestClient, due_order: date, sender: RecordingSender, session: Session
) -> None:
    no_content(register(client, OTHER_PHONE))
    third = "ef" * 32
    no_content(register(client, third))
    sender.answers = {
        OTHER_PHONE: Sent(Outcome.GONE, "Unregistered"),
        third: Sent(Outcome.BAD_TOKEN, "BadDeviceToken"),
    }

    reminders.remind(firing(due_order - timedelta(days=1)))

    assert sorted(token for token, _ in sender.sent) == sorted([PHONE, OTHER_PHONE, third])
    assert devices(session) == {PHONE: MOM}


def test_a_phone_reported_again_while_sending_is_kept(
    client: TestClient, due_order: date, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ReRegisteredMeanwhile:
        def send(self, token: str, notification: Notification) -> Sent:
            no_content(register(client, token, DAD))  # Dad signs in on it mid-send
            return Sent(Outcome.GONE, "Unregistered")

    monkeypatch.setattr(push, "get_sender", ReRegisteredMeanwhile)

    reminders.remind(firing(due_order - timedelta(days=1)))

    assert devices(session) == {PHONE: DAD}


def test_a_reminder_that_reached_no_phone_fails_so_it_is_retried(
    client: TestClient,
    due_order: date,
    sender: RecordingSender,
    session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender.answers = {PHONE: Sent(Outcome.FAILED, "503 ServiceUnavailable")}
    day = due_order - timedelta(days=1)

    with pytest.raises(reminders.ReminderNotSent):
        reminders.remind(firing(day))
    session.expire_all()
    assert session.get_one(AppUser, MOM).reminded_on is None
    [failed] = [r for r in caplog.records if r.message == "push.failed"]
    assert failed.levelno == logging.ERROR

    # Lambda's retry, with APNs back.
    sender.answers = {}
    reminders.remind(firing(day))
    assert len(sender.sent) == 2


def test_a_reminder_that_reached_one_phone_is_not_sent_again(
    client: TestClient, due_order: date, sender: RecordingSender
) -> None:
    no_content(register(client, OTHER_PHONE))
    sender.answers = {OTHER_PHONE: Sent(Outcome.FAILED, "503 ServiceUnavailable")}
    day = due_order - timedelta(days=1)

    reminders.remind(firing(day))
    reminders.remind(firing(day))

    assert sorted(token for token, _ in sender.sent) == sorted([PHONE, OTHER_PHONE])


# The real clients.


@pytest.fixture
def apns_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def apns(apns_key: str, answer: httpx2.Response) -> tuple[ApnsSender, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return answer

    sender = ApnsSender(apns_key, "KEY1234567")
    sender._client = httpx2.Client(base_url=push.APNS_URL, transport=httpx2.MockTransport(handle))
    return sender, requests


NOTE = Notification(
    kind=NotificationKind.IDEAS_READY,
    title="Your meal ideas are ready",
    body="20 ideas for Order 4.",
    expires_in=timedelta(hours=6),
    order_id=UUID(int=7),
)


def test_apns_gets_a_signed_alert_for_the_app(apns_key: str) -> None:
    sender, requests = apns(apns_key, httpx2.Response(200))

    assert sender.send(PHONE, NOTE) == Sent(Outcome.SENT)
    assert sender.send(PHONE, NOTE) == Sent(Outcome.SENT)

    first, second = requests
    assert first.url.path == f"/3/device/{PHONE}"
    assert first.headers["apns-topic"] == "com.conncastle.castlechef"
    assert first.headers["apns-push-type"] == "alert"
    assert "apns-collapse-id" not in first.headers
    bearer = first.headers["authorization"].removeprefix("bearer ")
    assert jwt.get_unverified_header(bearer)["kid"] == "KEY1234567"
    public = serialization.load_pem_private_key(apns_key.encode(), None).public_key()
    assert jwt.decode(bearer, public, algorithms=["ES256"])["iss"] == "EXAMP12345"
    # The signed token is reused, not made per request.
    assert second.headers["authorization"] == first.headers["authorization"]
    assert json.loads(first.content) == {
        "aps": {"alert": {"title": NOTE.title, "body": NOTE.body}, "sound": "default"},
        "kind": "ideas_ready",
        "order_id": str(UUID(int=7)),
    }


@pytest.mark.parametrize(
    ("answer", "sent"),
    [
        (httpx2.Response(410, json={"reason": "Unregistered"}), Sent(Outcome.GONE, "Unregistered")),
        (
            httpx2.Response(400, json={"reason": "BadDeviceToken"}),
            Sent(Outcome.BAD_TOKEN, "BadDeviceToken"),
        ),
        (
            httpx2.Response(403, json={"reason": "InvalidProviderToken"}),
            Sent(Outcome.FAILED, "403 InvalidProviderToken"),
        ),
        (httpx2.Response(500, text="oops"), Sent(Outcome.FAILED, "500 oops")),
    ],
)
def test_apns_answers_are_told_apart(apns_key: str, answer: httpx2.Response, sent: Sent) -> None:
    sender, _ = apns(apns_key, answer)
    assert sender.send(PHONE, NOTE) == sent


def test_the_apns_sender_is_built_from_the_base64_key(
    apns_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_PUSH", "apns")
    monkeypatch.setenv("CASTLE_CHEF_APNS_KEY", base64.b64encode(apns_key.encode()).decode())
    monkeypatch.setenv("CASTLE_CHEF_APNS_KEY_ID", "KEY1234567")
    push.get_sender.cache_clear()
    try:
        sender = push.get_sender()
        assert isinstance(sender, ApnsSender)
    finally:
        push.get_sender.cache_clear()


@pytest.fixture
def aws_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[AwsReminderScheduler, Stubber]]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    scheduler = AwsReminderScheduler(
        "castle-chef-reminders",
        "arn:aws:lambda:us-east-1:1:function:Reminders",
        "arn:aws:iam::1:role/SchedulerRole",
    )
    with Stubber(scheduler._scheduler) as stub:
        yield scheduler, stub
        stub.assert_no_pending_responses()


def test_a_new_schedule_is_created_whole(
    aws_scheduler: tuple[AwsReminderScheduler, Stubber],
) -> None:
    scheduler, stub = aws_scheduler
    user = UUID(int=1)
    schedule = {
        "Name": f"reminder-{user}",
        "GroupName": "castle-chef-reminders",
        "ScheduleExpression": "cron(5 7 * * ? *)",
        "ScheduleExpressionTimezone": ZONE,
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "State": "ENABLED",
        "ActionAfterCompletion": "NONE",
        "Target": {
            "Arn": "arn:aws:lambda:us-east-1:1:function:Reminders",
            "RoleArn": "arn:aws:iam::1:role/SchedulerRole",
            "Input": json.dumps(
                {
                    "version": 1,
                    "user_id": str(user),
                    "scheduled_time": "<aws.scheduler.scheduled-time>",
                }
            ),
            "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 900},
        },
    }
    stub.add_client_error("update_schedule", "ResourceNotFoundException", expected_params=schedule)
    stub.add_response("create_schedule", {"ScheduleArn": "arn:x"}, expected_params=schedule)
    # Next time it exists: updated with the same whole schedule.
    stub.add_response("update_schedule", {"ScheduleArn": "arn:x"}, expected_params=schedule)

    scheduler.upsert(user, time(7, 5), ZONE)
    scheduler.upsert(user, time(7, 5), ZONE)


def test_schedules_are_listed_and_deleted_by_member(
    aws_scheduler: tuple[AwsReminderScheduler, Stubber],
) -> None:
    scheduler, stub = aws_scheduler
    group = {"GroupName": "castle-chef-reminders", "NamePrefix": "reminder-"}
    listed = {
        "Arn": "arn:x",
        "Name": f"reminder-{UUID(int=1)}",
        "GroupName": "castle-chef-reminders",
        "State": "ENABLED",
        "Target": {"Arn": "arn:y"},
    }
    stub.add_response(
        "list_schedules", {"Schedules": [listed], "NextToken": "more"}, expected_params=group
    )
    stub.add_response(
        "list_schedules",
        {"Schedules": [listed | {"Name": f"reminder-{UUID(int=2)}"}]},
        expected_params=group | {"NextToken": "more"},
    )
    name = {"Name": f"reminder-{UUID(int=1)}", "GroupName": "castle-chef-reminders"}
    stub.add_client_error("delete_schedule", "ResourceNotFoundException", expected_params=name)
    stub.add_client_error("delete_schedule", "ThrottlingException", expected_params=name)

    assert scheduler.user_ids() == {UUID(int=1), UUID(int=2)}
    scheduler.delete(UUID(int=1))  # already gone: fine
    with pytest.raises(ScheduleError):
        scheduler.delete(UUID(int=1))


# The Reminders Lambda function's route.


@pytest.fixture
def reminder_events(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CASTLE_CHEF_REMINDERS_HANDLER", "true")
    get_settings.cache_clear()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_the_reminders_function_takes_firings_and_the_deploys_sync(
    client: TestClient,
    due_order: date,
    sender: RecordingSender,
    reminder_events: TestClient,
) -> None:
    fired = firing(due_order - timedelta(days=1)).model_dump(mode="json")

    assert ok(reminder_events.post("/events", json=fired)) == {"status": "handled"}
    assert len(sender.sent) == 1
    assert ok(reminder_events.post("/events", json={"version": 1, "sync": True})) == {
        "upserted": 1,
        "deleted": 2,
        "failed": 0,
    }


def test_the_reminders_function_fails_on_an_event_it_does_not_know(
    reminder_events: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    assert reminder_events.post("/events", json={"version": 2}).status_code == 500
    assert any(r.message == "reminders.bad_event" for r in caplog.records)


def test_the_public_api_has_no_reminders_route(client: TestClient) -> None:
    assert client.post("/events", json={"version": 1, "sync": True}).status_code == 404


def test_a_function_is_the_failure_handler_or_reminders_never_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASTLE_CHEF_REMINDERS_HANDLER", "true")
    monkeypatch.setenv("CASTLE_CHEF_FAILURE_HANDLER", "true")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="not both"):
        create_app()
