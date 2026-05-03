"""Multi-account drive against the bridge with rolling app passwords.

Runs the bridge as a local subprocess pointed at a real IMAPS endpoint
(default `mail.schuetze.io:993`), provisions N throwaway mailboxes via
the Mailcow admin API, then exercises the bridge in six passes:

  Pass 1 — concurrent baseline: all N users authenticate in parallel
  Pass 2 — rolling rotation: delete each app password + mint a new one
           in Mailcow, verify new works and old is rejected
  Pass 3 — IMAP unreachable: restart bridge pointed at a black-hole IP,
           verify the bridge fails closed (401, never grants)
  Pass 4 — mixed batch: half valid, half rotated-old in parallel,
           verify isolation (no cross-leakage)
  Pass 5 — broken creds: random garbage password per user, expect 401
  Pass 6 — unknown user: email that never existed, expect 401

Why Mailcow API not Authentik LDAP: this Mailcow setup uses Authentik LDAP
for the web UI only — Dovecot (IMAP) accepts only Mailcow-local app
passwords. So the only realistic IMAP-credentialed test path is to
provision mailboxes + app passwords via Mailcow's `/api/v1/add/...`.

Required env (script aborts if missing):
  MAILCOW_BASE      e.g. https://mail.schuetze.io
  MAILCOW_API_KEY   X-API-Key for Mailcow admin

Optional env:
  IMAP_HOST            default mail.schuetze.io
  IMAP_PORT            default 993
  N_USERS              default 3
  TEST_DOMAIN          default schuetze.io (must already exist in Mailcow)
  USER_PREFIX          default bridge-test-
  KEEP_USERS           1 = leave mailboxes in place after run
  BRIDGE_IMAP_TIMEOUT  default 15.0 seconds (Dovecot can be slow under load)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

# --- config ---------------------------------------------------------------

MAILCOW_BASE = os.environ.get("MAILCOW_BASE", "https://mail.schuetze.io").rstrip("/")
MAILCOW_API_KEY = os.environ.get("MAILCOW_API_KEY")
IMAP_HOST = os.environ.get("IMAP_HOST", "mail.schuetze.io")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
N_USERS = int(os.environ.get("N_USERS", "3"))
TEST_DOMAIN = os.environ.get("TEST_DOMAIN", "schuetze.io")
USER_PREFIX = os.environ.get("USER_PREFIX", "bridge-test-")
KEEP_USERS = os.environ.get("KEEP_USERS") == "1"

BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "18000"))
BRIDGE_ISSUER = f"http://127.0.0.1:{BRIDGE_PORT}"
BRIDGE_CLIENT_ID = "drive-test"
BRIDGE_CLIENT_SECRET = secrets.token_hex(32)
BRIDGE_SESSION_SECRET = secrets.token_hex(32)
BRIDGE_REDIRECT = "http://127.0.0.1:9999/cb"
BRIDGE_DATA = Path(os.environ.get("BRIDGE_DATA", "/tmp/bridge-drive-data"))
BRIDGE_IMAP_TIMEOUT = os.environ.get("BRIDGE_IMAP_TIMEOUT", "15.0")

IMAP_BLACKHOLE_HOST = "192.0.2.1"  # TEST-NET-1, RFC 5737, guaranteed unreachable


# --- mailcow api ----------------------------------------------------------


def mc(method: str, path: str, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.update({"X-API-Key": MAILCOW_API_KEY, "Content-Type": "application/json"})
    return requests.request(
        method,
        f"{MAILCOW_BASE}/api/v1{path}",
        headers=headers,
        timeout=30,
        **kwargs,
    )


def mc_check_response(r: requests.Response, what: str) -> None:
    """Mailcow returns 200 even on failures — body is `[{"type":"danger",...}]`."""
    if r.status_code >= 300:
        sys.exit(f"{what}: HTTP {r.status_code} {r.text[:200]}")
    body = r.json()
    if isinstance(body, list) and body and body[0].get("type") == "danger":
        sys.exit(f"{what}: {body[0].get('msg', body)}")


def create_mailbox(local_part: str, password: str) -> None:
    # Best-effort delete first so a half-finished previous run doesn't block us
    mc("POST", "/delete/mailbox", json=[f"{local_part}@{TEST_DOMAIN}"])
    r = mc("POST", "/add/mailbox", json={
        "local_part": local_part,
        "domain": TEST_DOMAIN,
        "name": f"Bridge Drive {local_part}",
        "quota": "100",
        "password": password,
        "password2": password,
        "active": "1",
        "force_pw_update": "0",
        "tls_enforce_in": "0",
        "tls_enforce_out": "0",
    })
    mc_check_response(r, f"add/mailbox {local_part}")


def delete_mailbox(email: str) -> None:
    # Mailcow expects an array body for DELETE /api/v1/delete/mailbox
    r = mc("POST", "/delete/mailbox", json=[email])
    mc_check_response(r, f"delete/mailbox {email}")


def add_app_password(email: str, app_pw: str, app_name: str) -> int:
    """Create app password. Returns the row id from mailcow's app_passwd table."""
    r = mc("POST", "/add/app-passwd", json={
        "username": email,
        "app_name": app_name,
        "app_passwd": app_pw,
        "app_passwd2": app_pw,
        "active": 1,
        "protocols": ["imap_access", "smtp_access"],
    })
    mc_check_response(r, f"add/app-passwd {email}")
    # Find the just-created row by listing
    return list_app_passwords(email).get(app_name, 0)


def list_app_passwords(email: str) -> dict[str, int]:
    """Return {app_name: id} for all app passwords on this mailbox."""
    r = mc("GET", f"/get/app-passwd/all/{email}")
    if r.status_code >= 300:
        return {}
    body = r.json()
    if not isinstance(body, list):
        return {}
    return {row["name"]: int(row["id"]) for row in body if row.get("name")}


def delete_app_password(row_id: int) -> None:
    r = mc("POST", "/delete/app-passwd", json=[str(row_id)])
    mc_check_response(r, f"delete/app-passwd {row_id}")


def flush_dovecot_auth_cache() -> None:
    """Mailcow's Dovecot caches auth results ~30s by default.

    After rotating an app password we need to flush the cache, otherwise
    the OLD password still appears to work — not a bridge bug, but it
    breaks the rotation regression test.
    """
    if os.environ.get("SKIP_REMOTE") == "1":
        return
    import contextlib
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             os.environ.get("REMOTE_DOCKER_HOST", "zkm-infra"),
             "docker exec mailcowdockerized-dovecot-mailcow-1 doveadm auth cache flush"],
            check=False, capture_output=True, timeout=30,
        )


# --- bridge subprocess ----------------------------------------------------


def start_bridge(imap_host: str = IMAP_HOST) -> subprocess.Popen:
    BRIDGE_DATA.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "IMAP_HOST": imap_host,
        "IMAP_PORT": str(IMAP_PORT),
        "IMAP_SSL": "true",
        "IMAP_TIMEOUT": BRIDGE_IMAP_TIMEOUT,
        "OIDC_ISSUER": BRIDGE_ISSUER,
        "OIDC_CLIENT_ID": BRIDGE_CLIENT_ID,
        "OIDC_CLIENT_SECRET": BRIDGE_CLIENT_SECRET,
        "OIDC_REDIRECT_URIS": json.dumps([BRIDGE_REDIRECT]),
        "SESSION_SECRET": BRIDGE_SESSION_SECRET,
        "DATA_DIR": str(BRIDGE_DATA),
        "BIND_HOST": "127.0.0.1",
        "BIND_PORT": str(BRIDGE_PORT),
        "LOG_LEVEL": "WARNING",
    })
    proc = subprocess.Popen(
        ["poetry", "run", "python", "-m", "imap_oidc_bridge"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[3],
    )
    for _ in range(60):
        try:
            if requests.get(f"{BRIDGE_ISSUER}/healthz", timeout=1).status_code == 200:
                return proc
        except requests.RequestException:
            pass
        time.sleep(0.5)
    proc.kill()
    err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
    sys.exit(f"bridge failed to start in 30s\nstderr:\n{err[-2000:]}")


def stop_bridge(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# --- bridge flow drivers --------------------------------------------------


def _begin_authorize(s: requests.Session) -> str:
    r = s.get(
        f"{BRIDGE_ISSUER}/authorize",
        params={
            "response_type": "code",
            "client_id": BRIDGE_CLIENT_ID,
            "redirect_uri": BRIDGE_REDIRECT,
            "scope": "openid email",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"GET /authorize -> {r.status_code}")
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("no request_id in form")
    return m.group(1)


def drive_full(email: str, password: str) -> tuple[bool, str]:
    s = requests.Session()
    try:
        rid = _begin_authorize(s)
    except RuntimeError as e:
        return False, str(e)

    r = s.post(
        f"{BRIDGE_ISSUER}/authorize",
        data={"request_id": rid, "email": email, "password": password},
        allow_redirects=False,
    )
    if r.status_code != 303:
        return False, f"POST /authorize -> {r.status_code}"
    code = parse_qs(urlparse(r.headers["Location"]).query)["code"][0]

    r = s.post(
        f"{BRIDGE_ISSUER}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": BRIDGE_REDIRECT,
            "client_id": BRIDGE_CLIENT_ID,
            "client_secret": BRIDGE_CLIENT_SECRET,
        },
    )
    if r.status_code != 200:
        return False, f"/token -> {r.status_code}: {r.text[:80]}"
    id_token = r.json()["id_token"]

    r = s.get(
        f"{BRIDGE_ISSUER}/userinfo",
        headers={"Authorization": f"Bearer {id_token}"},
    )
    if r.status_code != 200:
        return False, f"/userinfo -> {r.status_code}"
    body = r.json()
    if body.get("email") != email or body.get("sub") != email:
        return False, f"userinfo identity mismatch: {body}"
    return True, "OK"


def drive_expect_reject(email: str, password: str) -> tuple[bool, int, str]:
    s = requests.Session()
    try:
        rid = _begin_authorize(s)
    except RuntimeError as e:
        return False, 0, str(e)
    r = s.post(
        f"{BRIDGE_ISSUER}/authorize",
        data={"request_id": rid, "email": email, "password": password},
        allow_redirects=False,
    )
    return r.status_code == 401, r.status_code, r.text[:200]


# --- main -----------------------------------------------------------------


def main() -> int:
    if not MAILCOW_API_KEY:
        sys.exit("MAILCOW_API_KEY env var required")

    print("== bridge multi-account drive (Mailcow path)")
    print(f"   mailcow:      {MAILCOW_BASE}")
    print(f"   imap:         {IMAP_HOST}:{IMAP_PORT}")
    print(f"   bridge port:  {BRIDGE_PORT}")
    print(f"   N users:      {N_USERS}")
    print(f"   domain:       {TEST_DOMAIN}")

    print(f"\n[setup] provisioning {N_USERS} mailboxes + app passwords")
    users: list[dict] = []
    for i in range(1, N_USERS + 1):
        local = f"{USER_PREFIX}{i}"
        email = f"{local}@{TEST_DOMAIN}"
        mailbox_pw = secrets.token_urlsafe(20)
        app_pw = secrets.token_urlsafe(20)
        create_mailbox(local, mailbox_pw)
        app_id = add_app_password(email, app_pw, app_name="drive")
        users.append({"email": email, "password": app_pw, "app_id": app_id})
        print(f"  + {email} (app_passwd id={app_id})")

    # Mailcow takes a beat to commit the app password into Dovecot's lookup
    time.sleep(3)

    print(f"\n[setup] starting bridge subprocess (IMAP_HOST={IMAP_HOST})")
    bridge = start_bridge()

    failures: list[str] = []
    try:
        # ---------- pass 1: concurrent baseline ----------
        print(f"\n[pass 1] concurrent valid auth — {N_USERS} parallel")
        with ThreadPoolExecutor(max_workers=N_USERS) as pool:
            results = [
                (u["email"], pool.submit(drive_full, u["email"], u["password"]))
                for u in users
            ]
            for email, fut in results:
                ok, msg = fut.result()
                print(f"  {'✓' if ok else '✗'} {email}: {msg}")
                if not ok:
                    failures.append(f"pass1 {email}: {msg}")

        # ---------- pass 2: rolling rotation ----------
        print("\n[pass 2a] rolling app password rotation + new password works")
        for u in users:
            u["old_password"] = u["password"]
            u["old_app_id"] = u["app_id"]
            u["password"] = secrets.token_urlsafe(20)
            u["app_id"] = add_app_password(u["email"], u["password"], app_name=f"drive-{secrets.token_hex(2)}")
            delete_app_password(u["old_app_id"])
        flush_dovecot_auth_cache()  # otherwise old password still works for ~30s
        time.sleep(2)

        with ThreadPoolExecutor(max_workers=N_USERS) as pool:
            results = [
                (u["email"], pool.submit(drive_full, u["email"], u["password"]))
                for u in users
            ]
            for email, fut in results:
                ok, msg = fut.result()
                print(f"  {'✓' if ok else '✗'} new pw {email}: {msg}")
                if not ok:
                    failures.append(f"pass2a {email}: {msg}")

        print("\n[pass 2b] verifying OLD password is now rejected")
        for u in users:
            ok, status, body = drive_expect_reject(u["email"], u["old_password"])
            print(f"  {'✓' if ok else '✗'} old pw {u['email']}: bridge returned {status}")
            if not ok:
                failures.append(f"pass2b {u['email']}: status={status} body={body!r}")

        # ---------- pass 3: IMAP unreachable ----------
        print(f"\n[pass 3] IMAP unreachable — bridge restart pointed at {IMAP_BLACKHOLE_HOST}")
        stop_bridge(bridge)
        bridge = start_bridge(imap_host=IMAP_BLACKHOLE_HOST)
        for u in users[:2]:
            t0 = time.monotonic()
            ok, status, body = drive_expect_reject(u["email"], u["password"])
            dt = time.monotonic() - t0
            print(f"  {'✓' if ok else '✗'} {u['email']}: status={status} in {dt:.1f}s")
            if not ok:
                failures.append(f"pass3 {u['email']}: status={status}")
        stop_bridge(bridge)
        bridge = start_bridge()
        print(f"  bridge restored to {IMAP_HOST}")

        # ---------- pass 4: mixed batch ----------
        print("\n[pass 4] mixed batch — half valid, half old-rotated, parallel")
        valid_half = users[: N_USERS // 2]
        old_half = users[N_USERS // 2 :]
        tasks: list[tuple[dict, bool]] = (
            [(u, False) for u in valid_half] + [(u, True) for u in old_half]
        )

        def run(u: dict, use_old: bool) -> tuple[str, str, bool, str]:
            if use_old:
                ok, status, _ = drive_expect_reject(u["email"], u["old_password"])
                return u["email"], "old", ok, f"status={status}"
            ok, msg = drive_full(u["email"], u["password"])
            return u["email"], "new", ok, msg

        with ThreadPoolExecutor(max_workers=N_USERS) as pool:
            futures = [pool.submit(run, u, use_old) for u, use_old in tasks]
            for fut in as_completed(futures):
                email, kind, ok, info = fut.result()
                print(f"  {'✓' if ok else '✗'} {email} ({kind}): {info}")
                if not ok:
                    failures.append(f"pass4 {email} ({kind}): {info}")

        # ---------- pass 5: definitely-broken creds ----------
        print("\n[pass 5] definitely-broken creds — random garbage password")
        for u in users:
            garbage = "NOT-A-VALID-PASSWORD-" + secrets.token_urlsafe(8)
            ok, status, body = drive_expect_reject(u["email"], garbage)
            print(f"  {'✓' if ok else '✗'} {u['email']}: status={status}")
            if not ok:
                failures.append(f"pass5 {u['email']}: status={status} body={body!r}")

        # ---------- pass 6: unknown user ----------
        print("\n[pass 6] unknown user — never existed")
        ghost = f"ghost-{secrets.token_hex(4)}@{TEST_DOMAIN}"
        ok, status, body = drive_expect_reject(ghost, "anything")
        print(f"  {'✓' if ok else '✗'} {ghost}: status={status}")
        if not ok:
            failures.append(f"pass6 {ghost}: status={status} body={body!r}")
    finally:
        stop_bridge(bridge)
        if KEEP_USERS:
            print(f"\n[teardown] KEEP_USERS=1 — leaving {len(users)} mailboxes in place")
        else:
            print("\n[teardown] removing mailboxes (cascades app passwords)")
            for u in users:
                try:
                    delete_mailbox(u["email"])
                    print(f"  - {u['email']}")
                except Exception as e:
                    print(f"  ! delete {u['email']}: {e}")

    if failures:
        print(f"\nFAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  ! {f}")
        return 1
    print("\nALL PASSES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
