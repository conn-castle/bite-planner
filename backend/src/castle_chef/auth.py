"""Who is calling the API. The sign-in provider sits behind `AuthVerifier`, so it
can be replaced without touching the endpoints (only household membership, keyed
by the provider's user id, would need migrating)."""

import base64
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import jwt


@dataclass(frozen=True)
class Principal:
    """A signed-in user, identified by the sign-in provider's user id."""

    user_id: UUID


class AuthError(Exception):
    """The token is missing, malformed, expired, or not signed by the provider (401)."""


class AuthUnavailable(Exception):
    """The provider's signing keys could not be fetched, so no token can be checked (503)."""


class AuthVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class WebhookRejected(Exception):
    """A webhook request isn't Neon Auth's, as it is: unsigned, signed by another
    key, altered, or too old (401)."""


class WebhookVerifier(Protocol):
    def verify(self, body: bytes, headers: Mapping[str, str]) -> None: ...


def _jwks_client(auth_url: str) -> jwt.PyJWKClient:
    # An unknown key id refetches the keys at most once a minute (the cooldown).
    return jwt.PyJWKClient(
        f"{auth_url.rstrip('/')}/.well-known/jwks.json",
        lifespan=3600,
        timeout=5,
        cooldown_duration=60,
    )


class NeonAuthVerifier:
    """Verifies Neon Auth JWTs: EdDSA, signed by a key from the branch's JWKS.

    Keys are cached and refetched when a token names an unknown key id. If they
    can't be fetched, verification fails closed.
    """

    def __init__(
        self, auth_url: str, issuer: str | None = None, audience: str | None = None
    ) -> None:
        parts = urlsplit(auth_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        self.issuer = issuer or origin
        self.audience = audience or origin
        self._keys = _jwks_client(auth_url)

    def verify(self, token: str) -> Principal:
        try:
            key = self._keys.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as error:
            raise AuthUnavailable(
                f"Could not fetch the sign-in provider's keys: {error}"
            ) from error
        except jwt.PyJWTError as error:
            raise AuthError(f"Invalid token: {error}") from error
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                leeway=60,
                options={"require": ["sub", "exp", "iss"]},
            )
            return Principal(user_id=UUID(claims["sub"]))
        except (jwt.PyJWTError, ValueError) as error:
            raise AuthError(f"Invalid token: {error}") from error


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class NeonWebhookVerifier:
    """Verifies Neon Auth's webhook requests (https://neon.com/docs/auth/guides/webhooks):
    `X-Neon-Signature` is a detached JWS (EdDSA) over the request's timestamp and
    body, signed by a key from the branch's JWKS (`X-Neon-Signature-Kid`). Requests
    older than five minutes are refused, so one can't be replayed later."""

    MAX_AGE_MS = 5 * 60 * 1000

    def __init__(self, auth_url: str) -> None:
        self._keys = _jwks_client(auth_url)

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        signature = headers.get("x-neon-signature", "")
        kid = headers.get("x-neon-signature-kid", "")
        timestamp = headers.get("x-neon-timestamp", "")
        parts = signature.split(".")
        if len(parts) != 3 or parts[1] or not kid or not timestamp.isdigit():
            raise WebhookRejected("Not a signed Neon Auth request.")
        if abs(time.time() * 1000 - int(timestamp)) > self.MAX_AGE_MS:
            raise WebhookRejected("The request is too old.")
        # The signed payload is the timestamp and the body (both base64url), per RFC 7515.
        signed = _b64url(f"{timestamp}.{_b64url(body)}".encode())
        try:
            key = self._keys.get_signing_key(kid)
            jwt.PyJWS().decode(f"{parts[0]}.{signed}.{parts[2]}", key.key, algorithms=["EdDSA"])
        except jwt.PyJWKClientConnectionError as error:
            raise AuthUnavailable(f"Could not fetch Neon Auth's keys: {error}") from error
        except jwt.PyJWTError as error:
            raise WebhookRejected(f"Bad signature: {error}") from error
