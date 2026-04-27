from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class AuthCode:
    """Single-use authorization code, redeemable at /token."""

    code: str
    client_id: str
    redirect_uri: str
    email: str
    scope: str
    nonce: str | None
    state: str | None
    expires_at: float


class CodeStore:
    """In-memory authorization-code store with TTL.

    A real multi-replica deployment would back this with Redis; for a single
    container fronting one IMAP host this is fine — codes live ~60 seconds.
    """

    def __init__(self, ttl: int = 60) -> None:
        self._ttl = ttl
        self._codes: dict[str, AuthCode] = {}
        self._lock = Lock()

    def issue(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        email: str,
        scope: str,
        nonce: str | None,
        state: str | None,
    ) -> AuthCode:
        code = secrets.token_urlsafe(32)
        entry = AuthCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            email=email,
            scope=scope,
            nonce=nonce,
            state=state,
            expires_at=time.time() + self._ttl,
        )
        with self._lock:
            self._codes[code] = entry
        return entry

    def consume(self, code: str) -> AuthCode | None:
        now = time.time()
        with self._lock:
            entry = self._codes.pop(code, None)
            self._purge_expired(now)
        if entry is None or entry.expires_at < now:
            return None
        return entry

    def _purge_expired(self, now: float) -> None:
        expired = [c for c, e in self._codes.items() if e.expires_at < now]
        for c in expired:
            del self._codes[c]
