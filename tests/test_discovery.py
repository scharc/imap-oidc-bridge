from __future__ import annotations

from fastapi.testclient import TestClient


def test_discovery_document_shape(client: TestClient) -> None:
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "https://bridge.test"
    assert body["authorization_endpoint"].endswith("/authorize")
    assert body["token_endpoint"].endswith("/token")
    assert body["jwks_uri"].endswith("/jwks.json")
    assert "RS256" in body["id_token_signing_alg_values_supported"]
    assert "code" in body["response_types_supported"]


def test_jwks_has_rsa_key(client: TestClient) -> None:
    resp = client.get("/jwks.json")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    jwk = keys[0]
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"
    assert jwk["kid"]
    assert jwk["n"]
    assert jwk["e"]


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
