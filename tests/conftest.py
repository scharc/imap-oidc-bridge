from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imap_oidc_bridge.app import create_app
from imap_oidc_bridge.config import Settings
from imap_oidc_bridge.imap import IMAPAuthError


class FakeIMAP:
    """Stand-in for IMAPBackend used by tests; matches verify() signature."""

    def __init__(self, valid: dict[str, str]) -> None:
        self.valid = valid
        self.calls: list[tuple[str, str]] = []

    def verify(self, email: str, password: str) -> None:
        self.calls.append((email, password))
        if self.valid.get(email) != password:
            raise IMAPAuthError("invalid_credentials", "Invalid email or password.")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {
        "IMAP_HOST": "imap.test",
        "OIDC_ISSUER": "https://bridge.test",
        "OIDC_CLIENT_ID": "test-client",
        "OIDC_CLIENT_SECRET": "test-secret",
        "OIDC_REDIRECT_URIS": '["https://consumer.test/callback"]',
        "DATA_DIR": str(tmp_path),
        "SESSION_SECRET": "x" * 32,
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield Settings()  # type: ignore[call-arg, misc]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def fake_imap() -> FakeIMAP:
    return FakeIMAP({"alice@example.com": "hunter2"})


@pytest.fixture
def client(settings: Settings, fake_imap: FakeIMAP, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from imap_oidc_bridge import app as app_module

    monkeypatch.setattr(app_module, "IMAPBackend", lambda **kw: fake_imap)
    test_app = create_app(settings)
    with TestClient(test_app) as c:
        yield c
