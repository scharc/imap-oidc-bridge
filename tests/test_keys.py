from __future__ import annotations

from pathlib import Path

import pytest

from imap_oidc_bridge.keys import load_or_create


def test_creates_key_file_on_first_call(tmp_path: Path) -> None:
    target = tmp_path / "signing.pem"
    assert not target.exists()
    key = load_or_create(target)
    assert target.exists()
    assert target.stat().st_size > 0
    assert len(key.kid) == 16


def test_second_call_returns_same_kid(tmp_path: Path) -> None:
    target = tmp_path / "signing.pem"
    first = load_or_create(target)
    second = load_or_create(target)
    assert first.kid == second.kid


def test_jwk_shape(tmp_path: Path) -> None:
    key = load_or_create(tmp_path / "signing.pem")
    jwk = key.jwk()
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"
    assert jwk["kid"] == key.kid
    assert jwk["e"]
    assert jwk["n"]
    assert "=" not in jwk["n"]  # base64url, no padding


def test_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "signing.pem"
    load_or_create(target)
    assert target.exists()


def test_rejects_non_rsa_key_file(tmp_path: Path) -> None:
    target = tmp_path / "signing.pem"
    target.write_text("not a key")
    with pytest.raises(Exception):  # noqa: B017
        load_or_create(target)


def test_private_pem_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "signing.pem"
    key = load_or_create(target)
    pem = key.private_pem()
    assert b"BEGIN PRIVATE KEY" in pem
