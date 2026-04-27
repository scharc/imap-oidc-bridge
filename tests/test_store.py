from __future__ import annotations

import time

from imap_oidc_bridge.store import CodeStore


def _issue(store: CodeStore, **kw: object) -> str:
    defaults = {
        "client_id": "c",
        "redirect_uri": "https://x/cb",
        "email": "u@x",
        "scope": "openid",
        "nonce": None,
        "state": None,
    }
    defaults.update(kw)
    return store.issue(**defaults).code  # type: ignore[arg-type]


def test_issue_returns_distinct_codes() -> None:
    store = CodeStore(ttl=10)
    a = _issue(store)
    b = _issue(store)
    assert a != b
    assert len(a) >= 32


def test_consume_returns_entry_then_none() -> None:
    store = CodeStore(ttl=10)
    code = _issue(store, email="alice@x")
    first = store.consume(code)
    assert first is not None
    assert first.email == "alice@x"
    assert store.consume(code) is None


def test_consume_unknown_code_returns_none() -> None:
    assert CodeStore().consume("not-a-real-code") is None


def test_expired_code_is_not_returned(monkeypatch: object) -> None:
    store = CodeStore(ttl=1)
    code = _issue(store)
    # Force the entry's expiry into the past without sleeping
    entry = store._codes[code]
    entry.expires_at = time.time() - 1
    assert store.consume(code) is None


def test_purge_runs_on_consume_and_drops_expired_entries() -> None:
    store = CodeStore(ttl=10)
    keep = _issue(store, email="keep@x")
    drop = _issue(store, email="drop@x")
    store._codes[drop].expires_at = time.time() - 5
    # Consuming any code triggers a purge of expired entries
    store.consume("never")
    assert drop not in store._codes
    assert keep in store._codes
