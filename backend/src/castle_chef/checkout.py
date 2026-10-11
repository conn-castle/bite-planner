"""Checkout at the household's Instacart store, on the worker's side: what a quote
prices and what it read (typed JSONB on the job), the household's cart ledger,
and the household's Instacart website session, kept in SSM. The agent reads
checkout itself, in a browser, through castle_chef_agent.checkout's tools.

The session's cookies are secrets: they go from SSM to the job directory for the
tools and back, and are never logged.
"""

import base64
import gzip
import json
import logging
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from castle_chef_agent.address import Address
from castle_chef_agent.checkout import INSTACART_COOKIE, SITE, Checkout
from castle_chef_agent.windows import Window
from pydantic import BaseModel, ConfigDict, Field, model_validator

from castle_chef.settings import get_settings

log = logging.getLogger(__name__)


class CheckoutError(Exception):
    """Checkout couldn't be read; the message says why."""


# A signed-out run's error (the job's), which the app recognizes to say what to do.
SIGNED_OUT_ERROR = (
    "Castle Chef is signed out of Instacart's website: sign it in again "
    "(make instacart-login), then try again."
)


# What's kept (typed JSONB columns: models.Job, models.Household).


class CartLine(BaseModel):
    """A product in the cart and how many of it."""

    model_config = ConfigDict(frozen=True)

    product_id: str = Field(pattern=r"^\d+$")
    quantity: float = Field(gt=0)


class OwnItem(CartLine):
    """One of the household's own items in its cart, set aside during a run."""

    name: str


class CartLedger(BaseModel):
    """What a run did to the household's cart, until it's undone: the household's
    own items it set aside, and the products it put in (castle_chef.cart)."""

    set_aside: list[OwnItem]
    added: list[str]


class QuoteRequest(BaseModel):
    """What a quote prices: the list's products and counts, and the tip rule
    (household settings) it's read with."""

    model_config = ConfigDict(frozen=True)

    lines: list[CartLine] = Field(min_length=1)
    tip_percent: int
    tip_max_cents: int
    # Snapshot of the chosen saved address; part of the total the member approves.
    delivery_address: Address | None = None


class Quote(BaseModel):
    """A quote job's result: checkout's estimate, or why Instacart won't check
    the cart out (below the store's minimum), when nothing else is read."""

    blocked_because: str | None = None
    checkout: Checkout | None = None

    @model_validator(mode="after")
    def one_outcome(self) -> Quote:
        if (self.blocked_because is None) == (self.checkout is None):
            raise ValueError("A quote is either blocked or a checkout, not both or neither.")
        return self


class Swap(BaseModel):
    """A product gone when the order booked, and the alternative bought instead."""

    item: str  # the list's item ("yellow onions")
    gone: str  # the product that was gone
    bought: str  # the alternative bought


class Booking(BaseModel):
    """What Instacart confirmed for a booked order: its order number ("Dry run"
    for development's dry run), the window, and the total charged; each unknown
    for one settled by hand ("It was placed"). And any products swapped."""

    order_number: str | None
    window: Window | None
    total_cents: int | None
    swaps: list[Swap] = []


class BookIssue(BaseModel):
    """Why an approved order isn't booked (yet), as whoever approved it is told;
    for a product gone, the list items whose product to choose again."""

    message: str
    item_ids: list[UUID] = []


# The session.


class SessionStore:
    """The household's Instacart website session: browser storage state (its
    cookies), gzipped and base64 in one SSM SecureString parameter."""

    def __init__(self, parameter: str) -> None:
        import boto3  # the worker's group

        self.parameter = parameter
        self._ssm = boto3.client("ssm")

    def load(self) -> dict[str, Any]:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            reply = self._ssm.get_parameter(Name=self.parameter, WithDecryption=True)
        except (BotoCoreError, ClientError) as error:
            raise CheckoutError(f"Couldn't read the Instacart session from SSM: {error}") from error
        state: dict[str, Any] = json.loads(
            gzip.decompress(base64.b64decode(reply["Parameter"]["Value"]))
        )
        return state

    def save(self, cookies: Sequence[Mapping[str, Any]]) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        state = {"cookies": list(cookies), "origins": []}
        value = base64.b64encode(gzip.compress(json.dumps(state).encode(), 9)).decode()
        try:
            self._ssm.put_parameter(
                Name=self.parameter, Value=value, Type="SecureString", Overwrite=True
            )
        except (BotoCoreError, ClientError) as error:
            raise CheckoutError(f"Couldn't save the Instacart session to SSM: {error}") from error


def session_store() -> SessionStore:
    parameter = get_settings().instacart_session_parameter
    if parameter is None:
        raise CheckoutError(
            "No Instacart website session here (CASTLE_CHEF_INSTACART_SESSION_PARAMETER)."
        )
    return SessionStore(parameter)


def sign_in(store: str) -> None:
    """Sign Castle Chef in to Instacart's website: a browser opens on this machine
    (a new profile, removed after), the owner signs in to the household's account
    there and says so, and the session is saved to SSM for checkout."""
    from playwright.sync_api import sync_playwright

    sessions = session_store()
    # A profile of its own that goes with the browser: SSM keeps the only copy.
    with (
        tempfile.TemporaryDirectory(prefix="castle-chef-instacart-") as profile,
        sync_playwright() as playwright,
    ):
        context = playwright.chromium.launch_persistent_context(profile, headless=False)
        try:
            page = context.new_page()
            page.goto(f"{SITE}/store/{store.lower()}/storefront", wait_until="domcontentloaded")
            input(
                "Sign in to the household's Instacart account in the browser that opened, "
                "then press Enter here: "
            )
            cookies = [
                cookie
                for cookie in context.storage_state()["cookies"]
                if INSTACART_COOKIE.search(cookie["domain"].lstrip("."))
            ]
        finally:
            context.close()
    sessions.save(cookies)
    log.info("instacart.signed_in", extra={"cookies": len(cookies)})
