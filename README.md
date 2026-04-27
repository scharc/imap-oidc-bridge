# imap-oidc-bridge

A tiny OpenID Connect identity provider that authenticates against an IMAP
mailbox. It lets any OIDC consumer — Authentik, Keycloak, Dex, or an app's
built-in OIDC client — trust mailbox passwords as the source of truth, without
running an LDAP shim and without giving the upstream consumer your IMAP
credentials.

> **Status:** v0.1, single-tenant, single-client. Authorization code flow only.
> No refresh tokens, no PKCE yet. Suitable for low-volume self-hosted setups
> where the IMAP server is the existing user database.

## Why

You have a small group of users (a club, a team, a side project) whose
credentials already live in an IMAP mailbox — for example a cheap shared
webhosting tariff that gives you mail but no LDAP, no SCIM, and no API.
You want to put SSO in front of WordPress, Nextcloud, or anything else that
speaks OIDC, and you want one set of passwords.

This service does exactly one thing: when a user logs in, it does an `IMAP
LOGIN` against your configured IMAP server. If that succeeds, it issues an
ID token containing `sub=email` and `email_verified=true`. That's it.

## What it is not

- **Not a user directory.** IMAP gives you no enumeration, no groups, no
  attributes. Group assignment in your OIDC consumer must be policy-based
  (e.g. "everyone from this source belongs to group X").
- **Not a password store.** The bridge never persists passwords. Each login
  re-validates against IMAP.
- **Not an MFA provider.** Layer MFA in your OIDC consumer (Authentik does
  this cleanly), not here.
- **Not multi-tenant in v0.1.** One IMAP host, one OIDC client per container.
  Run multiple containers if you need multiple tenants.

## Endpoints

| Path                                | Purpose                          |
| ----------------------------------- | -------------------------------- |
| `/.well-known/openid-configuration` | OIDC discovery document          |
| `/jwks.json`                        | RSA public key (RS256)           |
| `/authorize`                        | Login form + auth-code redirect  |
| `/token`                            | Authorization-code → ID token    |
| `/userinfo`                         | Bearer-token → `sub`/`email`     |
| `/healthz`                          | Liveness probe                   |

## Quickstart (Docker)

```bash
docker run --rm \
  -p 8000:8000 \
  -v $PWD/data:/data \
  -e IMAP_HOST=mail.example.com \
  -e OIDC_ISSUER=https://imap-bridge.example.com \
  -e OIDC_CLIENT_ID=authentik \
  -e OIDC_CLIENT_SECRET=$(openssl rand -hex 32) \
  -e OIDC_REDIRECT_URIS='["https://sso.example.com/source/oauth/callback/imap-bridge/"]' \
  -e SESSION_SECRET=$(openssl rand -hex 32) \
  ghcr.io/marc-schuetze/imap-oidc-bridge:latest
```

A complete example with Traefik labels lives in
[`docker-compose.example.yml`](./docker-compose.example.yml).

## Configuration

All config is environment variables (see [`.env.example`](./.env.example)):

| Variable              | Required | Description                                                    |
| --------------------- | -------- | -------------------------------------------------------------- |
| `IMAP_HOST`           | yes      | IMAP hostname                                                  |
| `IMAP_PORT`           | no (993) | IMAP port                                                      |
| `IMAP_SSL`            | no (true)| Use IMAP4-SSL                                                  |
| `IMAP_TIMEOUT`        | no (10)  | Socket timeout in seconds                                      |
| `OIDC_ISSUER`         | yes      | Public URL of the bridge, no trailing slash                    |
| `OIDC_CLIENT_ID`      | yes      | The single OIDC client allowed to use this bridge              |
| `OIDC_CLIENT_SECRET`  | yes      | Long random string                                             |
| `OIDC_REDIRECT_URIS`  | yes      | JSON list of allowed redirect URIs                             |
| `OIDC_CODE_TTL`       | no (60)  | Authorization-code TTL in seconds                              |
| `OIDC_TOKEN_TTL`      | no (3600)| ID-token TTL in seconds                                        |
| `DATA_DIR`            | no (/data)| Where the RSA signing key is persisted                        |
| `SESSION_SECRET`      | yes      | Long random string for signing the in-flight auth state        |
| `LOG_LEVEL`           | no (INFO)| Python log level                                               |
| `BIND_HOST` / `BIND_PORT` | no   | Uvicorn bind                                                    |

## Wiring it into Authentik

See [`docs/authentik-setup.md`](./docs/authentik-setup.md) for a step-by-step
walkthrough.

The short version:
1. **Customization → Property Mappings → Create**: a Source property mapping
   that copies `info["email"]` into `properties["username"]` and
   `properties["email"]`.
2. **Directory → Federation & Social login → Create → OAuth Source** with
   provider type **OpenID Connect**, OIDC URL pointing at this bridge's
   `/.well-known/openid-configuration`, your `OIDC_CLIENT_ID` and
   `OIDC_CLIENT_SECRET`, the property mapping from step 1.
3. Set the **enrollment flow** to `default-source-enrollment` and the
   **authentication flow** to `default-source-authentication`.
4. Bind any application's access policy to a group, then assign newly
   enrolled users to that group via a group-membership policy on enrollment.

## Security model

- **Trust boundary:** the bridge speaks for whatever email the IMAP server
  accepts. If a user can `IMAP LOGIN` as `alice@example.com`, the ID token
  asserts `sub=alice@example.com`. There is no other identity check.
- **Email verification:** `email_verified=true` is asserted on the basis that
  the user proved current control of the mailbox. This is a strictly stronger
  claim than "this email exists", but weaker than "this person owns this
  identity in any external sense". Consumers should treat the bridge as the
  authoritative source for *that mailbox host only*.
- **Signing keys:** RS256 only. The RSA keypair is generated on first start
  and persisted to `${DATA_DIR}/signing.pem`. Mount a volume there. Rotating
  the key invalidates all outstanding tokens.
- **No password storage:** the bridge never writes passwords to disk and does
  not log them.
- **Rate limiting:** not implemented. Front the bridge with CrowdSec, fail2ban,
  Cloudflare, or whatever your reverse proxy provides.

## Limitations / roadmap

- v0.2: PKCE, refresh tokens, Hetzner-style autoconfig discovery.
- v0.3: multi-client (so one container can serve multiple consumers).
- v0.4: optional groups via a static membership map, for "all imap users → group X" without Authentik policies.
- Not planned: writes back to IMAP (account creation, password change). The
  bridge is read-only by design; account lifecycle stays in your mail
  provider's UI.

## Development

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run mypy imap_oidc_bridge
poetry run uvicorn imap_oidc_bridge.app:create_app --factory --reload
```

## License

MIT — see [LICENSE](./LICENSE).
