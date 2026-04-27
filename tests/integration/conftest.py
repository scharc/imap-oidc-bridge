"""Shared fixtures for docker-backed integration tests.

Spins up a real Greenmail mail server in a container so we exercise the
production `imaplib.IMAP4_SSL` / `imaplib.IMAP4` code paths against an
actual IMAP banner and protocol — not the FakeConn mock used in unit tests.

Greenmail's env-var user format only accepts a bare login (no `@`), so we
start the container with the management API enabled and create users via
HTTP after startup. That lets us configure `login = email`, mirroring how
production IMAP servers (Hetzner, dovecot with `auth_username_format=full`)
behave.

These tests are skipped unless explicitly selected via `pytest -m integration`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
import requests
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

USERS = [
    ("alice@example.com", "hunter2"),
    ("bob@example.com", "s3cret"),
]


def _wait_for_api(url: str, *, timeout: float = 15.0) -> None:
    """Poll until Greenmail's management API answers, or give up."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code < 500:
                return
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(0.25)
    raise TimeoutError(f"Greenmail API not ready at {url}: {last_exc}")


@pytest.fixture(scope="session")
def greenmail() -> Iterator[DockerContainer]:
    """Greenmail standalone with IMAP (3143) + management API (8080)."""
    container = (
        DockerContainer("greenmail/standalone:2.1.5")
        .with_env(
            "GREENMAIL_OPTS",
            (
                "-Dgreenmail.setup.test.imap "
                "-Dgreenmail.setup.test.api "
                "-Dgreenmail.hostname=0.0.0.0 "
                "-Dgreenmail.auth.disabled=false"
            ),
        )
        .with_exposed_ports(3143, 8080)
    )
    container.start()
    try:
        wait_for_logs(container, "API server at http", timeout=60)
        host = container.get_container_host_ip()
        api_port = int(container.get_exposed_port(8080))
        api_base = f"http://{host}:{api_port}/api/user"
        # The log line fires when the bind happens; the HTTP listener may
        # still need a beat before it accepts connections.
        _wait_for_api(api_base.replace("/api/user", "/api/service/readiness"))
        for email, password in USERS:
            resp = requests.post(
                api_base,
                json={"login": email, "email": email, "password": password},
                timeout=5,
            )
            resp.raise_for_status()
        yield container
    finally:
        container.stop()


@pytest.fixture
def greenmail_endpoint(greenmail: DockerContainer) -> tuple[str, int]:
    host = greenmail.get_container_host_ip()
    port = int(greenmail.get_exposed_port(3143))
    return host, port
