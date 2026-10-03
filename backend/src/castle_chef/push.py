"""Push notifications to people's phones: Apple's push service (APNs) on AWS, or a
log line in development (CASTLE_CHEF_PUSH). Sent straight to APNs with the key
from SSM; the phones' tokens are kept in `push_device` only."""

import base64
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from functools import lru_cache
from time import time
from typing import Protocol
from uuid import UUID

import httpx2
import jwt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from castle_chef.db import get_session
from castle_chef.models import PushDevice
from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

APNS_URL = "https://api.push.apple.com"  # production: TestFlight and App Store builds
TEAM_ID = "EXAMP12345"
TOPIC = "com.conncastle.castlechef"  # the app's bundle id
# Apple wants the signed token refreshed no more than every 20 minutes and no less
# than every 60.
TOKEN_REUSE_SECONDS = 50 * 60


class NotificationKind(StrEnum):
    REMINDER = "reminder"
    IDEAS_READY = "ideas_ready"
    LIST_READY = "list_ready"
    LIST_FAILED = "list_failed"  # shopping the list failed
    BOOKING = "booking"  # how booking an approved order went
    TEST = "test"  # `castle-chef household notify-test`


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: NotificationKind
    title: str
    body: str
    # How long APNs keeps it for a phone that's off or offline.
    expires_in: timedelta
    # A newer notification with the same id replaces the older one on the phone.
    collapse_id: str | None = None
    # What a tap opens (see the app's notifications.ts).
    order_id: UUID | None = None
    order_number: int | None = None


class Alert(BaseModel):
    title: str
    body: str


class Aps(BaseModel):
    alert: Alert
    sound: str = "default"


class Payload(BaseModel):
    """The APNs JSON: `aps` for iOS, the rest for the app."""

    aps: Aps
    kind: NotificationKind
    order_id: UUID | None = None
    order_number: int | None = None


def payload(notification: Notification) -> Payload:
    return Payload(
        aps=Aps(alert=Alert(title=notification.title, body=notification.body)),
        kind=notification.kind,
        order_id=notification.order_id,
        order_number=notification.order_number,
    )


class Outcome(StrEnum):
    SENT = "sent"
    # The token is no longer valid (e.g. the app was deleted).
    GONE = "gone"
    # APNs doesn't know the token: not a production token, or not ours.
    BAD_TOKEN = "bad_token"
    FAILED = "failed"


@dataclass(frozen=True)
class Sent:
    outcome: Outcome
    reason: str | None = None


class PushSender(Protocol):
    def send(self, token: str, notification: Notification) -> Sent: ...


class ApnsSender:
    """HTTP/2 to APNs with token-based auth: a JWT signed with the auth key."""

    def __init__(self, key_pem: str, key_id: str) -> None:
        self._key = key_pem
        self._key_id = key_id
        self._client = httpx2.Client(
            base_url=APNS_URL, http1=False, http2=True, timeout=httpx2.Timeout(10.0)
        )
        self._jwt: str | None = None
        self._jwt_at = 0.0

    def _bearer(self) -> str:
        now = time()
        if self._jwt is None or now - self._jwt_at > TOKEN_REUSE_SECONDS:
            self._jwt = jwt.encode(
                {"iss": TEAM_ID, "iat": int(now)},
                self._key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            self._jwt_at = now
        return self._jwt

    def send(self, token: str, notification: Notification) -> Sent:
        headers = {
            "authorization": f"bearer {self._bearer()}",
            "apns-topic": TOPIC,
            "apns-push-type": "alert",
            "apns-priority": "10",
            "apns-expiration": str(int(time() + notification.expires_in.total_seconds())),
        }
        if notification.collapse_id:
            headers["apns-collapse-id"] = notification.collapse_id
        try:
            response = self._client.post(
                f"/3/device/{token}",
                content=payload(notification).model_dump_json(exclude_none=True),
                headers=headers,
            )
        except httpx2.HTTPError as error:
            return Sent(Outcome.FAILED, f"{type(error).__name__}: {error}")
        if response.status_code == 200:
            return Sent(Outcome.SENT)
        reason = _reason(response)
        if response.status_code == 410:
            return Sent(Outcome.GONE, reason)
        if response.status_code == 400 and reason == "BadDeviceToken":
            return Sent(Outcome.BAD_TOKEN, reason)
        if reason in ("ExpiredProviderToken", "InvalidProviderToken"):
            self._jwt = None  # signed afresh next time
        return Sent(Outcome.FAILED, f"{response.status_code} {reason}")


def _reason(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    reason = body.get("reason") if isinstance(body, dict) else None
    return reason if isinstance(reason, str) else response.text[:200]


class LocalSender:
    """Development: logs what would be sent."""

    def send(self, token: str, notification: Notification) -> Sent:
        log.info(
            "push.local",
            extra={
                "device": token[-8:],
                "kind": notification.kind,
                "title": notification.title,
                "body": notification.body,
            },
        )
        return Sent(Outcome.SENT)


@lru_cache
def get_sender() -> PushSender:
    """The sender CASTLE_CHEF_PUSH selects."""
    settings = get_settings()
    if settings.push == "local":
        return LocalSender()
    if not settings.apns_key or not settings.apns_key_id:
        raise RuntimeError("Set CASTLE_CHEF_APNS_KEY and CASTLE_CHEF_APNS_KEY_ID.")
    return ApnsSender(base64.b64decode(settings.apns_key).decode(), settings.apns_key_id)


def register_device(session: Session, user_id: UUID, token: str) -> None:
    """Record that this phone gets this user's notifications: a phone someone else
    signed in on moves to them."""
    statement = insert(PushDevice).values(token=token, user_id=user_id)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[col(PushDevice.token)],
            set_={"user_id": statement.excluded.user_id, "updated_at": func.now()},
        )
    )
    session.commit()


def unregister_device(session: Session, user_id: UUID, token: str) -> None:
    """Stop sending this user's notifications to this phone (another user's phone
    is left alone)."""
    session.execute(
        delete(PushDevice).where(col(PushDevice.token) == token, col(PushDevice.user_id) == user_id)
    )
    session.commit()


@dataclass(frozen=True)
class Delivery:
    """How many of the user's phones a notification reached, and how many it
    couldn't (a phone APNs no longer knows is neither: it's forgotten)."""

    sent: int
    failed: int


def notify(user_id: UUID, notification: Notification) -> Delivery:
    """Send a notification to each of this user's phones. Failures are logged (an
    error trips the alarm); phones APNs no longer knows are forgotten. It raises
    only before sending to any phone (their phones can't be read, or the sender
    isn't configured)."""
    fields = {"user_id": str(user_id), "kind": notification.kind}
    with get_session() as session:
        devices = [
            (device.token, device.updated_at)
            for device in session.exec(
                select(PushDevice).where(PushDevice.user_id == user_id)
            ).all()
        ]
    if not devices:
        log.info("push.no_devices", extra=fields)
        return Delivery(sent=0, failed=0)
    sender = get_sender()
    sent_count = failed_count = 0
    for token, seen in devices:
        sent = sender.send(token, notification)
        device = fields | {"device": token[-8:], "reason": sent.reason}
        if sent.outcome is Outcome.SENT:
            sent_count += 1
            log.info("push.sent", extra=device)
            continue
        if sent.outcome is Outcome.FAILED:
            failed_count += 1
            log.error("push.failed", extra=device)
            continue
        if sent.outcome is Outcome.GONE:
            log.info("push.device_gone", extra=device)
        else:
            # Can also mean a development build's token, or the wrong APNs environment.
            log.warning("push.bad_device_token", extra=device)
        try:
            with get_session() as session:
                # Only as it was when sent: a phone reported again (or moved to
                # someone else) meanwhile is kept.
                session.execute(
                    delete(PushDevice).where(
                        col(PushDevice.token) == token,
                        col(PushDevice.user_id) == user_id,
                        col(PushDevice.updated_at) == seen,
                    )
                )
                session.commit()
        except Exception:
            # Sending has begun: failing now would have it retried for every phone.
            # APNs will reject the token again next time.
            log.exception("push.forget_failed", extra=device)
    return Delivery(sent=sent_count, failed=failed_count)
