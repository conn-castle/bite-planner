"""Neon Auth JWT verification, against a local JWKS endpoint and real Ed25519 keys."""

import base64
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import OKPAlgorithm

from bite_planner.api import app, get_verifier
from bite_planner.auth import (
    AuthError,
    AuthUnavailable,
    NeonAuthVerifier,
    NeonWebhookVerifier,
    WebhookRejected,
)
from bite_planner.settings import get_settings

SIGNING_KEY = Ed25519PrivateKey.generate()
KID = "test-key"


def jwks_for(private_key: Ed25519PrivateKey, kid: str) -> dict[str, Any]:
    jwk = json.loads(OKPAlgorithm.to_jwk(private_key.public_key()))
    return {"keys": [jwk | {"kid": kid, "alg": "EdDSA", "use": "sig"}]}


class JwksStandIn(BaseHTTPRequestHandler):
    """Neon Auth's JWKS endpoint, counting the times its keys are fetched."""

    body = json.dumps(jwks_for(SIGNING_KEY, KID)).encode()
    fetches = 0

    def do_GET(self) -> None:
        if self.path != "/neondb/auth/.well-known/jwks.json":
            self.send_error(404)
            return
        type(self).fetches += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture(scope="module")
def auth_url() -> Iterator[str]:
    """A Neon Auth stand-in serving only its JWKS."""
    server = HTTPServer(("127.0.0.1", 0), JwksStandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/neondb/auth"
    server.shutdown()


def origin(auth_url: str) -> str:
    return auth_url.removesuffix("/neondb/auth")


def token(
    auth_url: str, key: Ed25519PrivateKey = SIGNING_KEY, kid: str = KID, **claims: object
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(uuid4()),
        "iss": origin(auth_url),
        "aud": origin(auth_url),
        "iat": now,
        "exp": now + 900,
    } | claims
    return jwt.encode(payload, key, algorithm="EdDSA", headers={"kid": kid})


def test_valid_token_identifies_the_user(auth_url: str) -> None:
    user_id = uuid4()

    principal = NeonAuthVerifier(auth_url).verify(token(auth_url, sub=str(user_id)))

    assert principal.user_id == user_id


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 3600},  # expired
        {"iss": "https://someone-else.example"},
        {"aud": "https://someone-else.example"},
        {"sub": "not-a-uuid"},
    ],
    ids=["expired", "wrong-issuer", "wrong-audience", "bad-subject"],
)
def test_token_with_bad_claims_is_rejected(auth_url: str, claims: dict[str, object]) -> None:
    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify(token(auth_url, SIGNING_KEY, KID, **claims))


def test_token_without_a_subject_is_rejected(auth_url: str) -> None:
    now = int(time.time())
    unsigned = {"iss": origin(auth_url), "aud": origin(auth_url), "exp": now + 900}
    no_subject = jwt.encode(unsigned, SIGNING_KEY, algorithm="EdDSA", headers={"kid": KID})

    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify(no_subject)


def test_token_signed_by_another_key_is_rejected(auth_url: str) -> None:
    forged = token(auth_url, key=Ed25519PrivateKey.generate(), kid="unknown-key")

    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify(forged)


def test_token_reusing_the_key_id_with_another_key_is_rejected(auth_url: str) -> None:
    forged = token(auth_url, key=Ed25519PrivateKey.generate(), kid=KID)

    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify(forged)


def test_token_with_a_symmetric_algorithm_is_rejected(auth_url: str) -> None:
    # Algorithm confusion: an HS256 token "signed" with a shared secret.
    now = int(time.time())
    claims = {"sub": str(uuid4()), "iss": origin(auth_url), "aud": origin(auth_url)}
    forged = jwt.encode(
        claims | {"exp": now + 900},
        "a-guessable-but-long-enough-secret!",
        algorithm="HS256",
        headers={"kid": KID},
    )

    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify(forged)


def test_garbage_is_rejected(auth_url: str) -> None:
    with pytest.raises(AuthError):
        NeonAuthVerifier(auth_url).verify("not a token")


def test_the_api_fetches_the_signing_keys_once_not_on_every_request(
    auth_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BITE_PLANNER_NEON_AUTH_URL", auth_url)
    monkeypatch.setenv("BITE_PLANNER_NEON_AUTH_ISSUER", origin(auth_url))
    monkeypatch.setenv("BITE_PLANNER_NEON_AUTH_AUDIENCE", origin(auth_url))
    get_settings.cache_clear()
    get_verifier.cache_clear()  # one made for another test's settings
    client = TestClient(app)
    fetched = JwksStandIn.fetches

    try:
        for _ in range(2):
            response = client.get("/v1/me", headers={"Authorization": f"Bearer {token(auth_url)}"})
            assert response.status_code == 200, response.text
    finally:
        get_verifier.cache_clear()

    assert JwksStandIn.fetches - fetched == 1


def test_unreachable_keys_fail_closed() -> None:
    verifier = NeonAuthVerifier("http://127.0.0.1:9/neondb/auth")  # nothing listens here

    with pytest.raises(AuthUnavailable):
        verifier.verify(token("http://127.0.0.1:9/neondb/auth"))


# Neon Auth's webhook requests: a detached JWS over the timestamp and the body
# (https://neon.com/docs/auth/guides/webhooks), signed by a key in the same JWKS.


def neon_signed(
    body: bytes,
    key: Ed25519PrivateKey = SIGNING_KEY,
    kid: str = KID,
    timestamp: int | None = None,
) -> dict[str, str]:
    """The headers Neon Auth sends with a webhook request for this body."""
    sent = str(timestamp if timestamp is not None else int(time.time() * 1000))
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode()
    compact = jwt.PyJWS().encode(
        f"{sent}.{body_b64}".encode(), key, algorithm="EdDSA", headers={"kid": kid}
    )
    header, _, signature = compact.split(".")
    return {
        "x-neon-signature": f"{header}..{signature}",
        "x-neon-signature-kid": kid,
        "x-neon-timestamp": sent,
    }


BODY = b'{"event_type": "user.before_create", "user": {"email": "mom@example.com"}}'


def test_a_webhook_request_signed_by_neon_auth_is_accepted(auth_url: str) -> None:
    NeonWebhookVerifier(auth_url).verify(BODY, neon_signed(BODY))


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        # Altered on the way.
        (BODY.replace(b"mom", b"eve"), neon_signed(BODY)),
        # Signed by someone else's key under Neon's key id.
        (BODY, neon_signed(BODY, key=Ed25519PrivateKey.generate())),
        # Replayed long after.
        (BODY, neon_signed(BODY, timestamp=int(time.time() * 1000) - 10 * 60 * 1000)),
        # Not signed at all.
        (BODY, {}),
    ],
    ids=["altered", "other key", "stale", "unsigned"],
)
def test_webhook_requests_not_from_neon_auth_as_sent_are_refused(
    auth_url: str, body: bytes, headers: dict[str, str]
) -> None:
    with pytest.raises(WebhookRejected):
        NeonWebhookVerifier(auth_url).verify(body, headers)
