"""Who is calling the API. The sign-in provider sits behind `AuthVerifier`, so it
can be replaced without touching the endpoints (only household membership, keyed
by the provider's user id, would need migrating)."""

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
        # An unknown key id refetches the keys at most once a minute (the cooldown).
        self._keys = jwt.PyJWKClient(
            f"{auth_url.rstrip('/')}/.well-known/jwks.json",
            lifespan=3600,
            timeout=5,
            cooldown_duration=60,
        )

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
