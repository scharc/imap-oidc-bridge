from __future__ import annotations

import base64
import time
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient


def _start_authorize(client: TestClient, **overrides: str) -> str:
    params = {
        "response_type": "code",
        "client_id": "test-client",
        "redirect_uri": "https://consumer.test/callback",
        "scope": "openid email",
        "state": "abc",
        "nonce": "n-0",
    }
    params.update(overrides)
    resp = client.get("/authorize", params=params)
    assert resp.status_code == 200
    body = resp.text
    marker = 'name="request_id" value="'
    start = body.index(marker) + len(marker)
    return body[start : body.index('"', start)]


def _full_login(client: TestClient) -> str:
    rid = _start_authorize(client)
    redirect = client.post(
        "/authorize",
        data={"request_id": rid, "email": "alice@example.com", "password": "hunter2"},
        follow_redirects=False,
    )
    return parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]


# /authorize edge cases ---------------------------------------------------


def test_authorize_rejects_unsupported_response_type(client: TestClient) -> None:
    resp = client.get(
        "/authorize",
        params={
            "response_type": "token",
            "client_id": "test-client",
            "redirect_uri": "https://consumer.test/callback",
            "scope": "openid",
        },
    )
    assert resp.status_code == 400


def test_authorize_rejects_unknown_client(client: TestClient) -> None:
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "intruder",
            "redirect_uri": "https://consumer.test/callback",
            "scope": "openid",
        },
    )
    assert resp.status_code == 400


def test_authorize_requires_openid_scope(client: TestClient) -> None:
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "test-client",
            "redirect_uri": "https://consumer.test/callback",
            "scope": "email",
        },
    )
    assert resp.status_code == 400


def test_authorize_post_with_tampered_request_id_rejected(client: TestClient) -> None:
    resp = client.post(
        "/authorize",
        data={
            "request_id": "obviously-not-signed",
            "email": "alice@example.com",
            "password": "hunter2",
        },
    )
    assert resp.status_code == 400


def test_authorize_without_state_still_redirects(client: TestClient) -> None:
    rid = _start_authorize(client, state="")
    resp = client.post(
        "/authorize",
        data={"request_id": rid, "email": "alice@example.com", "password": "hunter2"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    qs = parse_qs(urlparse(resp.headers["location"]).query)
    assert "state" not in qs
    assert "code" in qs


# /token edge cases -------------------------------------------------------


def test_token_with_basic_auth_header(client: TestClient) -> None:
    code = _full_login(client)
    creds = base64.b64encode(b"test-client:test-secret").decode()
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://consumer.test/callback",
        },
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp.status_code == 200
    assert "id_token" in resp.json()


def test_token_with_basic_auth_wrong_secret(client: TestClient) -> None:
    code = _full_login(client)
    creds = base64.b64encode(b"test-client:nope").decode()
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://consumer.test/callback",
        },
        headers={"Authorization": f"Basic {creds}"},
    )
    assert resp.status_code == 401


def test_token_rejects_unsupported_grant_type(client: TestClient) -> None:
    resp = client.post(
        "/token",
        data={
            "grant_type": "password",
            "username": "alice@example.com",
            "password": "hunter2",
            "client_id": "test-client",
            "client_secret": "test-secret",
        },
    )
    assert resp.status_code == 400


def test_token_rejects_missing_code(client: TestClient) -> None:
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "redirect_uri": "https://consumer.test/callback",
            "client_id": "test-client",
            "client_secret": "test-secret",
        },
    )
    assert resp.status_code == 400


def test_token_rejects_redirect_uri_mismatch(client: TestClient) -> None:
    code = _full_login(client)
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://other.test/cb",
            "client_id": "test-client",
            "client_secret": "test-secret",
        },
    )
    assert resp.status_code == 400


# /userinfo edge cases ----------------------------------------------------


def test_userinfo_missing_bearer(client: TestClient) -> None:
    assert client.get("/userinfo").status_code == 401


def test_userinfo_garbage_bearer(client: TestClient) -> None:
    assert (
        client.get("/userinfo", headers={"Authorization": "Bearer garbage"}).status_code == 401
    )


def test_userinfo_token_signed_by_other_key(client: TestClient) -> None:
    """An attacker can't forge a token: signing with an unknown key fails verification."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    forged = jwt.encode(
        {"alg": "RS256"},
        {
            "iss": "https://bridge.test",
            "sub": "alice@example.com",
            "aud": "test-client",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "email": "alice@example.com",
        },
        pem,
    )
    if isinstance(forged, bytes):
        forged = forged.decode()
    resp = client.get("/userinfo", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_userinfo_expired_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    code = _full_login(client)
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://consumer.test/callback",
            "client_id": "test-client",
            "client_secret": "test-secret",
        },
    )
    id_token = resp.json()["id_token"]

    jwks = client.get("/jwks.json").json()
    jwk = JsonWebKey.import_key(jwks["keys"][0])
    claims = jwt.decode(id_token, jwk)
    # Manually validate with a clock that's far in the future to confirm expiry triggers.
    with pytest.raises(Exception):  # noqa: B017
        claims.validate(now=time.time() + 99999)


def test_discovery_lists_both_token_auth_methods(client: TestClient) -> None:
    body = client.get("/.well-known/openid-configuration").json()
    assert "client_secret_basic" in body["token_endpoint_auth_methods_supported"]
    assert "client_secret_post" in body["token_endpoint_auth_methods_supported"]
