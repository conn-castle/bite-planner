"""Neon Auth JWT verification, against a local JWKS endpoint and real Ed25519 keys."""

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
from jwt.algorithms import OKPAlgorithm

from castle_chef.auth import AuthError, AuthUnavailable, NeonAuthVerifier

SIGNING_KEY = Ed25519PrivateKey.generate()
KID = "test-key"


def jwks_for(private_key: Ed25519PrivateKey, kid: str) -> dict[str, Any]:
    jwk = json.loads(OKPAlgorithm.to_jwk(private_key.public_key()))
    return {"keys": [jwk | {"kid": kid, "alg": "EdDSA", "use": "sig"}]}


@pytest.fixture(scope="module")
def auth_url() -> Iterator[str]:
    """A Neon Auth stand-in serving only its JWKS."""
    body = json.dumps(jwks_for(SIGNING_KEY, KID)).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/neondb/auth/.well-known/jwks.json":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
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
        NeonAuthVerifier(auth_url).verify(token(auth_url, **claims))


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


def test_unreachable_keys_fail_closed() -> None:
    verifier = NeonAuthVerifier("http://127.0.0.1:9/neondb/auth")  # nothing listens here

    with pytest.raises(AuthUnavailable):
        verifier.verify(token("http://127.0.0.1:9/neondb/auth"))
