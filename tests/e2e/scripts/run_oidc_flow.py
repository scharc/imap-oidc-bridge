"""Drive the full OIDC authentication flow against the e2e stack.

Visits Authentik's source-login endpoint, follows the redirect chain to the
bridge's login form, submits valid IMAP credentials, follows the bridge's
redirect back to Authentik, and verifies that Authentik created (or
authenticated) a user with the expected email.

Run after `docker compose up -d` and `./seed-greenmail.sh`:
    poetry run python tests/e2e/scripts/run_oidc_flow.py
"""

from __future__ import annotations

import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

AUTHENTIK = "http://authentik.localtest.me:9000"
SOURCE_SLUG = "imap-bridge"
EMAIL = "alice@example.com"
PASSWORD = "hunter2"
ADMIN_TOKEN = "e2e-admin-token"


def negative_path() -> None:
    """Verify the bridge rejects requests that fail its own checks (tampered request_id).

    We can't test wrong-password against Greenmail here because Greenmail's IMAP
    server is intentionally permissive (it accepts any password). The
    wrong-password rejection path is covered by `tests/test_imap.py` against a
    mocked IMAP, which raises `imaplib.IMAP4.error` on bad creds.
    """
    print("\n[neg] tampered request_id is rejected at bridge")
    s = requests.Session()
    r = s.get(
        f"{AUTHENTIK}/source/oauth/login/{SOURCE_SLUG}/", allow_redirects=True
    )
    bridge = f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"
    r = s.post(
        f"{bridge}/authorize",
        data={"request_id": "not-a-signed-token", "email": EMAIL, "password": PASSWORD},
        allow_redirects=False,
    )
    if r.status_code != 400:
        sys.exit(f"      expected 400 on tampered request_id, got {r.status_code}")
    print("      bridge returned 400 (correct)")


def main() -> int:
    s = requests.Session()
    s.headers["User-Agent"] = "imap-oidc-bridge-e2e/1"

    print(f"[1/5] start at {AUTHENTIK}/source/oauth/login/{SOURCE_SLUG}/")
    r = s.get(
        f"{AUTHENTIK}/source/oauth/login/{SOURCE_SLUG}/",
        allow_redirects=True,
    )
    print(f"      landed at {r.url} (status {r.status_code})")
    if "imap-bridge.localtest.me" not in r.url:
        sys.exit(f"      expected to land on bridge /authorize, got {r.url}")

    print("[2/5] extract signed request_id from the bridge login form")
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    if not m:
        sys.exit(f"      no request_id in form. body[:500]={r.text[:500]}")
    request_id = m.group(1)

    print(f"[3/5] POST credentials to bridge /authorize as {EMAIL}")
    r = s.post(
        f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}/authorize",
        data={"request_id": request_id, "email": EMAIL, "password": PASSWORD},
        allow_redirects=False,
    )
    if r.status_code != 303:
        sys.exit(f"      expected 303, got {r.status_code}; body={r.text[:300]}")
    callback = r.headers["Location"]
    qs = parse_qs(urlparse(callback).query)
    print(f"      bridge redirected to {callback[:80]}... (code={qs['code'][0][:8]}…)")

    print("[4/6] follow callback into Authentik (it exchanges the code)")
    r = s.get(callback, allow_redirects=True)
    print(f"      landed at {r.url} (status {r.status_code})")
    if r.status_code != 200:
        sys.exit(f"      callback failed: {r.text[:300]}")

    print("[5/6] drive Authentik flow executor to completion")
    flow_query = urlparse(r.url).query
    flow_slug = urlparse(r.url).path.rstrip("/").rsplit("/", 1)[-1]
    while True:
        f = s.get(
            f"{AUTHENTIK}/api/v3/flows/executor/{flow_slug}/",
            params={"query": flow_query},
        )
        if f.status_code != 200:
            sys.exit(f"      flow executor returned {f.status_code}: {f.text[:300]}")
        challenge = f.json()
        component = challenge.get("component")
        print(f"      stage component={component}")
        if component == "xak-flow-redirect":
            target = challenge.get("to", "")
            if target.startswith("/if/user") or target.startswith("/if/admin"):
                break  # success — Authentik wants to land us on a user page
            # follow other redirects in case there are more flow stages
            r = s.get(target if target.startswith("http") else f"{AUTHENTIK}{target}")
            flow_query = urlparse(r.url).query
            continue
        sys.exit(f"      unexpected challenge: {challenge}")

    print("[6/6] verify our session is bound to the seeded user")
    me = s.get(f"{AUTHENTIK}/api/v3/core/users/me/")
    if me.status_code != 200:
        sys.exit(f"      /core/users/me/ returned {me.status_code}: {me.text[:200]}")
    user = me.json().get("user", {})
    if user.get("email") != EMAIL:
        sys.exit(f"      session bound to {user.get('email')!r}, expected {EMAIL!r}")
    print(f"      session.user.email={user['email']!r} username={user['username']!r}")

    print("\nOK — full OIDC round-trip succeeded; user logged in via IMAP creds.")
    negative_path()
    print("OK — negative path also clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
