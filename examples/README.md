# Examples

Copy-pasteable, production-shaped configs for wiring the bridge into common
setups.

## `authentik-blueprint.yaml`

Complete Authentik blueprint that creates everything you need:

- **Source** — `imap-bridge` OAuth source pointing at the bridge's discovery
  URL.
- **Group** — `imap-bridge-users`, anyone who logs in through the bridge
  ends up here (via the source's enrollment flow defaults).
- **Provider** — example confidential OIDC provider (`cloud-provider`)
  with the standard openid/email/profile scope mappings.
- **Application** — `cloud`, surfaced in Authentik's user UI.
- **Policy binding** — only members of `imap-bridge-users` can reach
  the application.

### Apply

```bash
# On the Authentik host, copy into the blueprints mount
cp examples/authentik-blueprint.yaml /opt/docker/authentik/blueprints/custom/

# Edit the placeholder URLs and secrets first (look for example.com)
$EDITOR /opt/docker/authentik/blueprints/custom/authentik-blueprint.yaml

# Authentik picks up new blueprints on the next worker tick (~minute);
# restart the worker if you want it immediate
docker compose restart authentik-worker
```

### What to swap

| Placeholder                          | Replace with                                            |
| ------------------------------------ | ------------------------------------------------------- |
| `https://imap-bridge.example.com`    | public URL of your bridge                               |
| `https://sso.example.com`            | public URL of your Authentik                            |
| `https://cloud.example.com`          | public URL of the downstream app you're protecting      |
| `imap-bridge` / `IMAP-BRIDGE-…`      | the bridge's `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`    |
| `cloud-client-id` / `CLOUD-…`        | the app's OIDC client credentials                       |

### Adapting

- **Multiple downstream apps** — duplicate the provider + application +
  policybinding triple, give each new one a distinct `client_id`,
  `slug`, and redirect URI.
- **Multiple bridges** (one per tenant) — duplicate the source +
  group with distinct slugs.
- **Brand-scoped** — once you have a `Brand`, set the source's
  `Available for` to it so only that brand's login page surfaces it.

The corresponding admin-UI walkthrough lives in
[`../docs/authentik-setup.md`](../docs/authentik-setup.md). The
end-to-end test stack in [`../tests/e2e/`](../tests/e2e/) uses a
narrower variant of the same blueprint that you can crib from too.
