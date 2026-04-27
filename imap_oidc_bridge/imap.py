from __future__ import annotations

import contextlib
import imaplib
import socket
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


class IMAPAuthError(Exception):
    """Raised when IMAP authentication fails for any reason."""


@dataclass(slots=True)
class IMAPBackend:
    host: str
    port: int = 993
    ssl: bool = True
    timeout: float = 10.0

    def verify(self, email: str, password: str) -> None:
        """Verify credentials by performing IMAP LOGIN. Raises IMAPAuthError on failure.

        We deliberately do not return additional metadata: IMAP gives us no
        attributes beyond "the LOGIN succeeded", so the caller can only
        trust the email the user typed.
        """
        if not email or not password:
            raise IMAPAuthError("empty credentials")

        try:
            if self.ssl:
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                    host=self.host, port=self.port, timeout=self.timeout
                )
            else:
                conn = imaplib.IMAP4(host=self.host, port=self.port, timeout=self.timeout)
        except (OSError, socket.gaierror) as exc:
            log.warning("imap.connect_failed", host=self.host, error=str(exc))
            raise IMAPAuthError("upstream IMAP unreachable") from exc

        try:
            try:
                conn.login(email, password)
            except imaplib.IMAP4.error as exc:
                log.info("imap.login_rejected", email=email, error=str(exc))
                raise IMAPAuthError("invalid credentials") from exc
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
