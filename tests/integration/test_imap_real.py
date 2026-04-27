"""IMAP protocol-level integration tests against a real Greenmail server.

These exist to prove the production `imaplib`-based code path works end-to-end
against a real IMAP banner, real socket lifecycle, real LOGIN command. They
are *not* the right place to verify negative-auth behaviour: Greenmail's IMAP
service is intentionally permissive (it's a fake mail server for testing
mail flow, not a credential validator) and will accept arbitrary users and
passwords. Negative-auth behaviour is covered by `tests/test_imap.py` against
a mocked `imaplib`.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from imap_oidc_bridge.imap import IMAPAuthError, IMAPBackend

pytestmark = pytest.mark.integration


def test_real_imap_login_succeeds(greenmail_endpoint: tuple[str, int]) -> None:
    host, port = greenmail_endpoint
    backend = IMAPBackend(host=host, port=port, ssl=False, timeout=15.0)
    backend.verify("alice@example.com", "hunter2")  # no exception = success


def test_real_imap_concurrent_logins(greenmail_endpoint: tuple[str, int]) -> None:
    """Many parallel verifies must all succeed and not leak sockets."""
    host, port = greenmail_endpoint
    backend = IMAPBackend(host=host, port=port, ssl=False, timeout=15.0)

    def one() -> bool:
        backend.verify("alice@example.com", "hunter2")
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: one(), range(16)))
    assert all(results)


def test_real_imap_second_user(greenmail_endpoint: tuple[str, int]) -> None:
    host, port = greenmail_endpoint
    backend = IMAPBackend(host=host, port=port, ssl=False, timeout=15.0)
    backend.verify("bob@example.com", "s3cret")


def test_real_imap_unreachable_host_times_out_quickly() -> None:
    """A wrong port should fail fast, not hang for the OS default. No greenmail needed."""
    backend = IMAPBackend(host="127.0.0.1", port=1, ssl=False, timeout=2.0)
    started = time.monotonic()
    with pytest.raises(IMAPAuthError, match="upstream"):
        backend.verify("u@x", "pw")
    assert time.monotonic() - started < 5.0


def test_real_imap_empty_credentials_short_circuit() -> None:
    """No greenmail needed — must fail before opening a socket."""
    backend = IMAPBackend(host="127.0.0.1", port=1, ssl=False, timeout=2.0)
    started = time.monotonic()
    with pytest.raises(IMAPAuthError, match="empty"):
        backend.verify("", "pw")
    # If we'd actually opened a socket to :1 it would have taken a connect-refused round-trip.
    assert time.monotonic() - started < 0.5
