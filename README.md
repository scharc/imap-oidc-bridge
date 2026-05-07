# imap-oidc-bridge

A tiny OpenID Connect identity provider that authenticates against an IMAP
mailbox. It lets any OIDC consumer — Authentik, Keycloak, Dex, or an app's
built-in OIDC client — trust mailbox passwords as the source of truth, without
running an LDAP shim and without giving the upstream consumer your IMAP
credentials.

> **Status:** v0.2.1 — alpha. Single-tenant, single-client, authorization
> code flow only. No refresh tokens, no PKCE yet. Suitable for low-volume
> self-hosted setups where the IMAP server is the existing user database.
> Tested end-to-end against Authentik 2024.10 + Greenmail and against
> a real Mailcow with multi-account drives (concurrent + rolling rotation
> + IMAP-down + broken-creds passes); soaking, not yet promoted to stable.

## Why

**Built for Hetzner Webhosting.** Hetzner's shared webhosting tariffs give
you mailboxes but no LDAP, no SCIM, no API — not even an unofficial one.
That makes them impossible to plug into Authentik or any other OIDC IdP
through normal means. This bridge is the workaround: it speaks OIDC on
the front and `IMAP LOGIN` on the back, so a club / team / side project
on a €5/month Hetzner Webhosting plan can put SSO in front of WordPress,
Nextcloud, or anything else that speaks OIDC, with the mail credentials
the users already have.

It works against any IMAP server — the design is generic — but Hetzner
Webhosting is the original use case and what shapes the trade-offs (no
batch user-creation, no group attributes, single-tenant container).

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

No prebuilt image is published yet — this is a source-only alpha. Build it
yourself:

```bash
git clone https://github.com/scharc/imap-oidc-bridge.git
cd imap-oidc-bridge
docker build -t imap-oidc-bridge:local .

docker run --rm \
  -p 8000:8000 \
  -v $PWD/data:/data \
  -e IMAP_HOST=mail.example.com \
  -e OIDC_ISSUER=https://imap-bridge.example.com \
  -e OIDC_CLIENT_ID=authentik \
  -e OIDC_CLIENT_SECRET=$(openssl rand -hex 32) \
  -e OIDC_REDIRECT_URIS='["https://sso.example.com/source/oauth/callback/imap-bridge/"]' \
  -e SESSION_SECRET=$(openssl rand -hex 32) \
  imap-oidc-bridge:local
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

### Branding the login form

The form is the **front door for every user** of a deployment, so it needs to
look like part of the tenant's stack. It ships with neutral defaults and pulls
no external assets unless you opt in — fonts come from the OS, colors switch
automatically with `prefers-color-scheme`, and there is no logo or footer
unless you set one.

All branding is opt-in via environment variables. Defaults shown below in
parentheses; everything is optional.

#### Identity

| Variable          | Default       | Use                                                        |
| ----------------- | ------------- | ---------------------------------------------------------- |
| `BRAND_LOGO_URL`  | *(none)*      | URL of an SVG/PNG shown above the form (a wordmark works). |
| `BRAND_TITLE`     | `Sign in`     | Heading text.                                              |
| `BRAND_SUBTEXT`   | *(generic)*   | One-line hint under the heading.                           |

#### Free-HTML content slots

Three slots let you inject arbitrary HTML at three positions in the form.
Content is rendered verbatim (operator-supplied = trusted), so you can
include `<a>`, `<strong>`, inline SVG, etc. — anything safe in your tenant
context.

| Variable                | Position                                                      |
| ----------------------- | ------------------------------------------------------------- |
| `BRAND_HEADER_HTML`     | Above the card (welcome banner, alert, club name).            |
| `BRAND_BELOW_FORM_HTML` | Between the submit button and the footer (help text, links).  |
| `BRAND_FOOTER_HTML`     | Footer text (replaces the default IMAP-trust line if set).    |

#### Structured footer links (Impressum, Datenschutz, …)

| Variable             | Format                                                        |
| -------------------- | ------------------------------------------------------------- |
| `BRAND_FOOTER_LINKS` | JSON list of `{"label": "…", "href": "…"}` pairs.             |

Renders as a centered `·`-separated row in the footer. Useful for the EU
legal-page links every German-speaking tenant needs:

```bash
BRAND_FOOTER_LINKS='[{"label":"Impressum","href":"https://example.com/impressum"},{"label":"Datenschutz","href":"https://example.com/datenschutz"}]'
```

The bridge does **not** host the legal pages itself — link to whichever
public URL your tenant already serves them from (their main website, a
static-site stack, etc.). Keeps tenant content out of the bridge's release
cycle.

#### Visual

| Variable               | Use                                                                       |
| ---------------------- | ------------------------------------------------------------------------- |
| `BRAND_ACCENT_COLOR`   | CSS color for the submit button + focus ring (e.g. `#7c3aed`).            |
| `BRAND_BACKGROUND_URL` | Optional full-page background image (rendered behind a contrast overlay). |

#### String overrides (lighter than full i18n)

For any deployment that needs labels in another language, override individual
strings rather than dragging in a translation framework. Defaults are English.

| Variable                       | Default                                                       |
| ------------------------------ | ------------------------------------------------------------- |
| `BRAND_LANG`                   | `en` (sets `<html lang>` for screen readers + browser locale) |
| `BRAND_LABEL_EMAIL`            | `Email`                                                       |
| `BRAND_LABEL_PASSWORD`         | `Password`                                                    |
| `BRAND_LABEL_SUBMIT`           | `Sign in`                                                     |
| `BRAND_LABEL_SUBMITTING`       | `Signing in…`                                                 |
| `BRAND_PLACEHOLDER_EMAIL`      | `you@example.com`                                             |
| `BRAND_ARIA_FOOTER_LINKS`      | `Site links` (screen-reader label for the footer-links nav)   |
| `BRAND_ERROR_EMPTY_CREDENTIALS`    | `Email and password are required.`                        |
| `BRAND_ERROR_INVALID_CREDENTIALS`  | `Invalid email or password.`                              |
| `BRAND_ERROR_UPSTREAM_UNREACHABLE` | `Mail server is currently unreachable. Please try again.` |
| `BRAND_ERROR_UPSTREAM_ERROR`       | `Mail server returned an error.`                          |

For German, set `BRAND_LANG=de`, `BRAND_TITLE=Anmelden`, `BRAND_LABEL_EMAIL=E-Mail`,
`BRAND_LABEL_PASSWORD=Passwort`, `BRAND_LABEL_SUBMIT=Anmelden`,
`BRAND_LABEL_SUBMITTING=Anmelden…`, plus the four `BRAND_ERROR_*` strings —
and the form is fully localized including the IMAP-failure banner.

#### Worked example

A real-world deployment for a hypothetical "imsteinig" tenant:

```bash
BRAND_LOGO_URL=https://logo.example.org/logo.svg?sub=imsteinig
BRAND_TITLE=Anmelden
BRAND_SUBTEXT=Mit deinem imsteinig.de-Postfach anmelden.
BRAND_BELOW_FORM_HTML=Probleme? Schreib an <a href="mailto:admin@imsteinig.de">admin@imsteinig.de</a>.
BRAND_FOOTER_LINKS=[{"label":"Impressum","href":"https://imsteinig.de/impressum"},{"label":"Datenschutz","href":"https://imsteinig.de/datenschutz"}]
BRAND_ACCENT_COLOR=#0ea5e9
BRAND_LABEL_EMAIL=E-Mail
BRAND_LABEL_PASSWORD=Passwort
BRAND_LABEL_SUBMIT=Anmelden
BRAND_LABEL_SUBMITTING=Anmelden…
```

## Wiring it into Authentik

Two paths:

- **Click-through** — see [`docs/authentik-setup.md`](./docs/authentik-setup.md)
  for the admin-UI walkthrough.
- **GitOps / declarative** — drop
  [`examples/authentik-blueprint.yaml`](./examples/authentik-blueprint.yaml)
  into Authentik's `/blueprints/custom/` mount, edit the placeholder URLs +
  secrets, and the source / group / provider / application are all created
  for you on the next worker tick.

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

## Decommissioning a user

The bridge is stateless — it has no user table to prune. To remove a user
from your stack you must delete them in **two places**, and the bridge
handles neither:

1. **At the IMAP server** — delete (or disable) the mailbox. The next
   `IMAP LOGIN` will then fail and the bridge will return 401 to anyone
   trying to authenticate. New tokens stop being issued.
2. **At the OIDC consumer** (Authentik / Keycloak / your app) — delete or
   disable the user record. This is what kills any *existing* session and
   ensures a token still in flight can't be used to start a new one.

Step 2 matters because tokens already issued by the bridge stay valid
until `OIDC_TOKEN_TTL` expires (default 3600 s). The bridge has no
revocation endpoint by design — short-lived tokens + active consumer-side
deprovisioning are the right primitives, not stateful revocation lists.

For tighter blast-radius:

- **Lower the token TTL.** Set `OIDC_TOKEN_TTL=300` (5 min) or even `60`.
  A deleted user is then locked out within that window even if the
  consumer hasn't run its deprovisioning loop yet. Trades a bit of
  re-auth churn for a much tighter window.
- **Run a periodic sync.** A small cron that compares your OIDC
  consumer's user list to the IMAP server's mailbox list, and disables
  consumer users whose mailbox no longer exists. ~50 LoC, lives outside
  the bridge. Not provided here — it's environment-specific.

Order of operations when offboarding:
delete the mailbox first, wait for any in-flight token to expire (or one
TTL window), then delete the consumer user. Reverse order leaves a
window where the user can still log in via the bridge but the consumer
won't accept the new identity — confusing error messages.

## Limitations / roadmap

- v0.2.1 (now): source-only — no prebuilt image is published. Build from
  the `Dockerfile` in your own environment. Login-form branding: logo,
  HTML content slots (header / below-form / footer), structured footer
  links (Impressum etc.), accent color, optional background image, per-
  string overrides + per-error overrides for any-language deployments
  (`<html lang>` + IMAP-error banner translations included).
- v0.3: PKCE, refresh tokens, Hetzner-style autoconfig discovery.
- v0.4: multi-client (so one container can serve multiple consumers).
- v0.5: optional groups via a static membership map, for "all imap users → group X" without Authentik policies.
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
