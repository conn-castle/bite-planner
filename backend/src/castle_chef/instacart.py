"""The household's Instacart store, as the worker reaches it (castle_chef_agent.instacart
with this environment's settings), and connecting it: signing Codex in to the
household's ChatGPT account and connecting the Instacart app there."""

import fcntl
import logging
import shlex
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from castle_chef_agent.address import Address
from castle_chef_agent.instacart import (
    AppServer,
    Instacart,
    InstacartError,
    codex_lock,
    connect,
)
from pydantic import BaseModel, ValidationError

from castle_chef.settings import get_settings

log = logging.getLogger(__name__)

# Instacart's app in ChatGPT: the plugin to install, from OpenAI's curated marketplace.
INSTACART_PLUGIN = "plugin_asdk_app_691eab1e001081919e57189f8b2f03bc"
INSTACART_MARKETPLACE = "openai-curated-remote"

# How long the owner has to approve the sign-in and connect Instacart, both steps
# together: within the worker task's 20-minute cap (docker/Dockerfile).
APPROVAL_TIMEOUT_SECONDS = 15 * 60
POLL_SECONDS = 10


class _DeviceCode(BaseModel):
    userCode: str
    verificationUrl: str


class _LoginCompleted(BaseModel):
    success: bool
    error: str | None = None


class _AppNeedingAuth(BaseModel):
    name: str
    installUrl: str


class _Installed(BaseModel):
    appsNeedingAuth: list[_AppNeedingAuth]


def _parse[T: BaseModel](model: type[T], reply: object, what: str) -> T:
    try:
        return model.model_validate(reply)
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in error.errors())
        raise InstacartError(
            f"{what} wasn't as expected (missing or invalid: {fields})."
        ) from error


def _codex_home() -> Path:
    home = get_settings().codex_home
    if home is None:
        raise InstacartError(
            "No Codex login here (CASTLE_CHEF_CODEX_HOME): Instacart is reached only from "
            "the worker image."
        )
    return home


@contextmanager
def job_lock(job_id: str) -> Iterator[None]:
    """Queue Instacart jobs before their 15-minute agent timer starts.

    The tool server takes the Codex login lock during startup. A separate lock
    here makes a job wait behind another job's cart or login before its runner
    starts, so the wait does not consume the model's time limit.
    """
    home = get_settings().codex_home
    if home is None:
        yield
        return
    started = time.monotonic()
    with (home / "job.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        log.info(
            "instacart.job_lock_acquired",
            extra={"job_id": job_id, "waited_seconds": round(time.monotonic() - started, 1)},
        )
        yield


@contextmanager
def store(address: Address | None = None) -> Iterator[Instacart]:
    """The household's store at Instacart, for the length of the block."""
    settings = get_settings()
    with connect(
        _codex_home(),
        settings.instacart_store,
        lock_file=settings.codex_lock_file,
        command=shlex.split(settings.codex_command),
        uid=settings.agent_uid,
        address=address,
    ) as instacart:
        yield instacart


def login() -> int:
    """Sign Codex in to the household's ChatGPT account (a device code, if it isn't
    signed in) and connect the Instacart app there (a link the owner opens in that
    account), printing what the owner does, until the store's cart answers.
    Returns 0 once it does, 1 if a step failed or timed out."""
    settings = get_settings()
    deadline = time.monotonic() + APPROVAL_TIMEOUT_SECONDS
    try:
        with (
            job_lock("login"),
            codex_lock(settings.codex_lock_file, settings.agent_uid),
            AppServer(
                _codex_home(), shlex.split(settings.codex_command), 120, settings.agent_uid
            ) as server,
        ):
            if server.request("account/read", {"refreshToken": False}).get("account") is None:
                start = _parse(
                    _DeviceCode,
                    server.request("account/login/start", {"type": "chatgptDeviceCode"}),
                    "The sign-in's device code",
                )
                print(f"Sign in to the household's ChatGPT account: {start.verificationUrl}")
                print(f"Code: {start.userCode}", flush=True)
                done = _parse(
                    _LoginCompleted,
                    server.wait_for(
                        lambda m: m.get("method") == "account/login/completed",
                        max(deadline - time.monotonic(), 0),
                    ).get("params"),
                    "The sign-in's result",
                )
                if not done.success:
                    raise InstacartError(f"ChatGPT sign-in failed: {done.error}")
                log.info("codex.signed_in")
            installed = _parse(
                _Installed,
                server.request(
                    "plugin/install",
                    {
                        "pluginName": INSTACART_PLUGIN,
                        "remoteMarketplaceName": INSTACART_MARKETPLACE,
                        "installAttemptId": str(uuid4()),
                    },
                ),
                "Installing the Instacart app",
            )
            for app in installed.appsNeedingAuth:
                print(
                    f"Connect {app.name} in the same ChatGPT account: {app.installUrl}", flush=True
                )
            # Connected once Instacart answers: its tools work from AWS, while ChatGPT's
            # app listing (app/list) is behind a bot check there.
            store = Instacart(server, settings.instacart_store)
            while True:
                try:
                    cart = store.cart()
                    break
                except InstacartError as error:
                    if time.monotonic() > deadline:
                        raise InstacartError(
                            f"Instacart wasn't connected in time: {error}"
                        ) from error
                time.sleep(POLL_SECONDS)
    except InstacartError as error:
        log.error("instacart.connect_failed", extra={"error": str(error)})
        print(error, file=sys.stderr)
        return 1
    log.info("instacart.connected", extra={"cart_items": len(cart.items)})
    print(
        f"Instacart is connected: the {settings.instacart_store} cart has {len(cart.items)} items."
    )
    return 0
