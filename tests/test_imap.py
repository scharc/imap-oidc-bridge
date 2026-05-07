from __future__ import annotations

import imaplib
import socket
from typing import Any

import pytest

from imap_oidc_bridge import imap as imap_module
from imap_oidc_bridge.imap import IMAPAuthError, IMAPBackend


class FakeConn:
    def __init__(
        self,
        *,
        login_ok: bool = True,
        logout_raises: bool = False,
        login_raises: BaseException | None = None,
    ) -> None:
        self.login_ok = login_ok
        self.logout_raises = logout_raises
        self.login_raises = login_raises
        self.login_called_with: tuple[str, str] | None = None
        self.logout_called = False

    def login(self, email: str, password: str) -> None:
        self.login_called_with = (email, password)
        if self.login_raises is not None:
            raise self.login_raises
        if not self.login_ok:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def logout(self) -> None:
        self.logout_called = True
        if self.logout_raises:
            raise OSError("connection reset")


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"connect_raises": None, "conn": None}

    class FakeIMAP:
        # Keep imaplib.IMAP4.error pointing at the real exception class so
        # `except imaplib.IMAP4.error` in the production code still resolves.
        error = imaplib.IMAP4.error

        def __new__(cls, **kwargs: Any) -> FakeConn:  # type: ignore[misc]
            if state["connect_raises"] is not None:
                raise state["connect_raises"]
            state["last_kwargs"] = kwargs
            return state["conn"] or FakeConn()

    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(imap_module.imaplib, "IMAP4", FakeIMAP)
    return state


def test_verify_success(patched: dict[str, Any]) -> None:
    conn = FakeConn(login_ok=True)
    patched["conn"] = conn
    IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert conn.login_called_with == ("u@x", "pw")
    assert conn.logout_called is True


def test_verify_login_rejected(patched: dict[str, Any]) -> None:
    patched["conn"] = FakeConn(login_ok=False)
    with pytest.raises(IMAPAuthError, match="Invalid email") as ei:
        IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert ei.value.code == "invalid_credentials"


def test_verify_connection_failure(patched: dict[str, Any]) -> None:
    patched["connect_raises"] = OSError("connection refused")
    with pytest.raises(IMAPAuthError, match="unreachable") as ei:
        IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert ei.value.code == "upstream_unreachable"


def test_verify_dns_failure(patched: dict[str, Any]) -> None:
    patched["connect_raises"] = socket.gaierror("name or service not known")
    with pytest.raises(IMAPAuthError):
        IMAPBackend(host="nope").verify("u@x", "pw")


def test_verify_empty_credentials_short_circuit() -> None:
    backend = IMAPBackend(host="mail.x")
    with pytest.raises(IMAPAuthError, match="required") as ei:
        backend.verify("", "pw")
    assert ei.value.code == "empty_credentials"
    with pytest.raises(IMAPAuthError, match="required"):
        backend.verify("u@x", "")


def test_logout_failure_is_swallowed(patched: dict[str, Any]) -> None:
    """A failing logout must not propagate after a successful login."""
    patched["conn"] = FakeConn(login_ok=True, logout_raises=True)
    IMAPBackend(host="mail.x").verify("u@x", "pw")  # no exception


def test_logout_runs_even_after_login_failure(patched: dict[str, Any]) -> None:
    conn = FakeConn(login_ok=False)
    patched["conn"] = conn
    with pytest.raises(IMAPAuthError):
        IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert conn.logout_called is True


def test_ssl_false_uses_plain_imap4(patched: dict[str, Any]) -> None:
    patched["conn"] = FakeConn()
    IMAPBackend(host="mail.x", ssl=False).verify("u@x", "pw")
    # Both factory entries point at the same fn so just verify last kwargs were sane
    assert patched["last_kwargs"]["host"] == "mail.x"


def test_timeout_passed_through(patched: dict[str, Any]) -> None:
    patched["conn"] = FakeConn()
    IMAPBackend(host="mail.x", timeout=2.5).verify("u@x", "pw")
    assert patched["last_kwargs"]["timeout"] == 2.5


def test_login_timeout_returns_authn_error_not_500(patched: dict[str, Any]) -> None:
    """Regression: SSL read timeout during LOGIN must convert to IMAPAuthError.

    Previously TimeoutError escaped to FastAPI as a 500. Verifies the bridge
    fails closed with a 401 to the consumer instead, and still cleans up
    the socket via logout().
    """
    conn = FakeConn(login_raises=TimeoutError("The read operation timed out"))
    patched["conn"] = conn
    with pytest.raises(IMAPAuthError, match="returned an error") as ei:
        IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert ei.value.code == "upstream_error"
    assert conn.logout_called is True


def test_login_connection_reset_returns_authn_error(patched: dict[str, Any]) -> None:
    """Mid-LOGIN socket drop (ConnectionResetError is OSError) → IMAPAuthError."""
    conn = FakeConn(login_raises=ConnectionResetError("Connection reset by peer"))
    patched["conn"] = conn
    with pytest.raises(IMAPAuthError, match="returned an error") as ei:
        IMAPBackend(host="mail.x").verify("u@x", "pw")
    assert ei.value.code == "upstream_error"
    assert conn.logout_called is True
