from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient


def _start_authorize(client: TestClient, **overrides: str) -> str:
    params = {
        "response_type": "code",
        "client_id": "test-client",
        "redirect_uri": "https://consumer.test/callback",
        "scope": "openid email",
        "state": "abc",
        "nonce": "n-0S6_WzA2Mj",
    }
    params.update(overrides)
    resp = client.get("/authorize", params=params)
    assert resp.status_code == 200
    # Form embeds the signed pending blob in the request_id input
    body = resp.text
    marker = 'name="request_id" value="'
    start = body.index(marker) + len(marker)
    end = body.index('"', start)
    return body[start:end]


def test_full_authorization_code_flow(client: TestClient) -> None:
    request_id = _start_authorize(client)

    resp = client.post(
        "/authorize",
        data={
            "request_id": request_id,
            "email": "alice@example.com",
            "password": "hunter2",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    qs = parse_qs(urlparse(location).query)
    assert qs["state"] == ["abc"]
    code = qs["code"][0]

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
    assert resp.status_code == 200
    tokens = resp.json()
    id_token = tokens["id_token"]

    jwks = client.get("/jwks.json").json()
    jwk = JsonWebKey.import_key(jwks["keys"][0])
    claims = jwt.decode(id_token, jwk)
    claims.validate()
    assert claims["sub"] == "alice@example.com"
    assert claims["email"] == "alice@example.com"
    assert claims["aud"] == "test-client"
    assert claims["iss"] == "https://bridge.test"
    assert claims["nonce"] == "n-0S6_WzA2Mj"

    ui = client.get("/userinfo", headers={"Authorization": f"Bearer {id_token}"})
    assert ui.status_code == 200
    assert ui.json()["sub"] == "alice@example.com"


def test_invalid_imap_credentials_rerender_form(client: TestClient) -> None:
    request_id = _start_authorize(client)
    resp = client.post(
        "/authorize",
        data={
            "request_id": request_id,
            "email": "alice@example.com",
            "password": "wrong",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "invalid credentials" in resp.text


def test_unknown_redirect_uri_rejected(client: TestClient) -> None:
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "test-client",
            "redirect_uri": "https://evil.test/cb",
            "scope": "openid",
        },
    )
    assert resp.status_code == 400


def test_code_can_only_be_used_once(client: TestClient) -> None:
    request_id = _start_authorize(client)
    redirect = client.post(
        "/authorize",
        data={
            "request_id": request_id,
            "email": "alice@example.com",
            "password": "hunter2",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]

    common = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://consumer.test/callback",
        "client_id": "test-client",
        "client_secret": "test-secret",
    }
    assert client.post("/token", data=common).status_code == 200
    assert client.post("/token", data=common).status_code == 400


def test_token_endpoint_requires_client_secret(client: TestClient) -> None:
    request_id = _start_authorize(client)
    redirect = client.post(
        "/authorize",
        data={
            "request_id": request_id,
            "email": "alice@example.com",
            "password": "hunter2",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(redirect.headers["location"]).query)["code"][0]

    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://consumer.test/callback",
            "client_id": "test-client",
            "client_secret": "wrong",
        },
    )
    assert resp.status_code == 401
