# End-to-end tests against a real Authentik

This directory contains a docker-compose stack that brings up Authentik,
postgres, redis, Greenmail (fake mail server with IMAP), and the bridge —
wired together so you can prove the entire OIDC chain works.

The e2e flow we exercise:

```
browser ─▶ Authentik /source/oauth/login/imap-bridge/
        ◀─ 302
browser ─▶ Bridge /authorize        (login form)
browser ─▶ Bridge /authorize POST   (alice@example.com / hunter2)
        ─▶ Bridge does IMAP LOGIN against Greenmail
        ◀─ 303 to Authentik /source/oauth/callback/...?code=...
Authentik ─▶ Bridge /token         (exchanges code)
Authentik ─▶ Bridge /jwks.json     (verifies RS256 signature)
Authentik   matches alice@example.com to seeded user via email_link
browser ─▶ Authentik flow executor → user is logged in
```

## Prerequisites

- Docker + `docker compose`
- Free ports 9000 (Authentik), 8181 (bridge), 33143 (Greenmail IMAP), 38080
  (Greenmail API), and the internal postgres/redis ports (no host bind).
- `*.localtest.me` resolves to 127.0.0.1 publicly, so no `/etc/hosts`
  changes are needed.

## One-shot run

```bash
# from the repo root
docker compose -f tests/e2e/docker-compose.yml up -d --build
./tests/e2e/scripts/seed-greenmail.sh
poetry run python tests/e2e/scripts/run_oidc_flow.py
docker compose -f tests/e2e/docker-compose.yml down -v
```

The script asserts:

1. The bridge's discovery doc + login form are reachable.
2. POSTing valid IMAP credentials returns a 303 with an OAuth code.
3. Authentik's callback exchanges the code at `/token` without error,
   meaning the RS256 ID token signature verified against `/jwks.json`.
4. The flow executor advances to `xak-flow-redirect → /if/user/`,
   meaning Authentik finished login.
5. `GET /api/v3/core/users/me/` returns the seeded `alice@example.com`,
   meaning the session is bound to the right user.
6. Tampered `request_id` is rejected with HTTP 400 at the bridge.

## Browsing the stack manually

After `docker compose up`:

- Authentik admin: <http://authentik.localtest.me:9000>
  (login: `akadmin` / `e2e-admin`)
- Bridge discovery: <http://imap-bridge.localtest.me:8181/.well-known/openid-configuration>
- Greenmail mgmt API: <http://localhost:38080/api/user>

To trigger the flow in a browser, visit:
<http://authentik.localtest.me:9000/source/oauth/login/imap-bridge/>

## Limitations

**Greenmail accepts any password** — it's a fake mail server intended for
mail-flow testing, not credential validation. Negative-auth coverage lives in
`tests/test_imap.py` against a mocked `imaplib`, where we can verify the
bridge handles `imaplib.IMAP4.error` correctly. The wrong-password path
through the full stack is therefore not asserted here.

**Pre-seeded users** — the blueprint creates `alice` and `bob` so that
Authentik's `email_link` matching has something to match. In production you
would either pre-seed your user list the same way or customise the
enrollment flow to auto-create without interactive confirmation.

**Not in CI** — this stack takes ~30s to come up and depends on
network-pulled images (Authentik, postgres, redis, Greenmail). It runs
locally on demand, not in the standard `pytest` run.
