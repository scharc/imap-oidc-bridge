# Wiring imap-oidc-bridge into Authentik

This walks through adding the bridge as an OAuth (OIDC) source in Authentik
2024.x. Steps are written against the admin UI; the same can be done via
Authentik blueprints if you prefer GitOps.

## Prerequisites

- A running `imap-oidc-bridge` instance, reachable at e.g.
  `https://imap-bridge.example.com`.
- Authentik admin access at e.g. `https://sso.example.com`.
- A long random `OIDC_CLIENT_SECRET` known to both sides.

## 1. Pick a slug

Decide on a slug for the source — e.g. `imap-bridge`. The redirect URI
Authentik will use for this source is then:

```
https://sso.example.com/source/oauth/callback/imap-bridge/
```

Add this URI to the bridge's `OIDC_REDIRECT_URIS`.

## 2. Create the OAuth source

**Directory → Federation & Social login → Create → OAuth Source**

| Field                      | Value                                                                  |
| -------------------------- | ---------------------------------------------------------------------- |
| Name                       | IMAP Bridge                                                            |
| Slug                       | `imap-bridge`                                                          |
| User matching mode         | `Link to a user with identical email` (or `Use the username from the source`) |
| Provider type              | `OpenID Connect`                                                       |
| Consumer key               | Your `OIDC_CLIENT_ID`                                                  |
| Consumer secret            | Your `OIDC_CLIENT_SECRET`                                              |
| OIDC well-known URL        | `https://imap-bridge.example.com/.well-known/openid-configuration`     |
| Authentication flow        | `default-source-authentication`                                        |
| Enrollment flow            | `default-source-enrollment`                                            |

Save.

## 3. (Optional) Property mapping for the username

By default Authentik will use whatever the OIDC `sub` says for the username.
If you'd rather use the local-part of the email instead of the full address,
add a Source Property Mapping:

**Customization → Property Mappings → Create → Scope Mapping (Source)**

```python
return {
    "username": info["email"].split("@")[0],
    "email": info["email"],
    "name": info["email"].split("@")[0],
}
```

Attach it to the source under **User property mappings**.

## 4. Group assignment policy

Since IMAP gives no group information, you typically want to assign every
user enrolled via this source to a fixed group (e.g. `imsteinig-members`).

1. **Directory → Groups**: create the group if it doesn't exist.
2. **Customization → Policies → Create → Expression Policy**

   ```python
   from authentik.core.models import Group
   user = request.user
   if user and request.context.get("source", {}).get("slug") == "imap-bridge":
       group, _ = Group.objects.get_or_create(name="imsteinig-members")
       user.ak_groups.add(group)
   return True
   ```

3. Bind this policy to the **enrollment flow** of the source, after the
   `default-source-enrollment-write` stage.

Alternatively, do it manually for the first few users and skip the policy.

## 5. Per-tenant auto-redirect (recommended for multi-tenant setups)

Steps 1–4 above wire the source so it appears as a "Sign in with …" button
on Authentik's default login form. For multi-tenant deployments — where
the same Authentik serves operators, other tenants, and the bridge users
side by side — that surface gets crowded fast, and federated users
(whose only credential is the IMAP password) face a confusing Email/Password
form that *always fails* for them, with the source button as a secondary
action they have to discover.

A **per-tenant authentication flow** with one Identification stage
configured `user_fields=[]` + this bridge as the single source causes
Authentik to **auto-redirect** to the bridge: zero buttons, zero clicks.
End users on the tenant's apps land directly on the bridge's mailbox
login page; other users (operators, other tenants) keep using the default
flow unchanged.

### Set it up (admin UI)

1. **Flows & Stages → Stages → Create → Identification Stage**

   | Field                         | Value                                              |
   | ----------------------------- | -------------------------------------------------- |
   | Name                          | `<tenant>-authentication-identification`           |
   | User fields                   | *(none — leave the list empty)*                    |
   | Sources                       | the OAuth source you created in §2                 |
   | Show source labels            | *(any — irrelevant when user_fields is empty)*     |
   | Password stage                | *(none)*                                           |
   | Pretend user exists           | *(off)*                                            |

2. **Flows & Stages → Flows → Create → Flow**

   | Field           | Value                                |
   | --------------- | ------------------------------------ |
   | Name            | `<tenant>-authentication-flow`       |
   | Slug            | `<tenant>-authentication-flow`       |
   | Title           | something user-friendly              |
   | Designation     | Authentication                       |
   | Authentication  | None (form is reachable unauth'd)    |

3. **On the new flow → Stage Bindings → Create**

   | Order | Stage                                            |
   | ----- | ------------------------------------------------ |
   | 10    | `<tenant>-authentication-identification` (from §1) |
   | 30    | `default-authentication-login` (the built-in user-login stage) |

4. **For each tenant service's OIDC Provider** (Applications → Providers
   → edit) — set the **Authentication flow** field to the new
   `<tenant>-authentication-flow`. (Leave **Authorization flow** as your
   existing consent flow.)

That's it. Test by visiting one of the tenant apps in a private window —
the OIDC handshake should land directly on the bridge's login form
without any Authentik UI in between.

### Set it up (REST API, scriptable)

For ops-as-code or when scaling to many tenants. Replace `<TOKEN>`,
`<TENANT>`, `<SOURCE_PK>` (your `imap-bridge` source's pk), and the
provider pks at the bottom.

```bash
TOKEN='<your-Authentik-API-token>'
BASE='https://sso.example.com/api/v3'

# 1. Identification stage (single source, no user input)
STAGE_PK=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  "$BASE/stages/identification/" \
  -d '{
    "name": "<TENANT>-authentication-identification",
    "user_fields": [],
    "password_stage": null,
    "sources": ["<SOURCE_PK>"],
    "show_source_labels": true,
    "pretend_user_exists": false
  }' | jq -r .pk)

# 2. The flow itself
FLOW_PK=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  "$BASE/flows/instances/" \
  -d '{
    "name": "<TENANT>-authentication-flow",
    "slug": "<TENANT>-authentication-flow",
    "title": "Welcome to <TENANT>",
    "designation": "authentication",
    "authentication": "none",
    "policy_engine_mode": "any"
  }' | jq -r .pk)

# 3. Bind identification @ order 10
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  "$BASE/flows/bindings/" \
  -d "{\"target\":\"$FLOW_PK\",\"stage\":\"$STAGE_PK\",\"order\":10,\"evaluate_on_plan\":true}"

# 4. Bind built-in user-login @ order 30
USER_LOGIN_PK=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/stages/user_login/?name=default-authentication-login" \
  | jq -r '.results[0].pk')

curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  "$BASE/flows/bindings/" \
  -d "{\"target\":\"$FLOW_PK\",\"stage\":\"$USER_LOGIN_PK\",\"order\":30,\"evaluate_on_plan\":true}"

# 5. Repoint each tenant-service OIDC provider's authentication_flow
for PROVIDER_PK in 28 29 30 31; do
  curl -s -X PATCH -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    "$BASE/providers/oauth2/$PROVIDER_PK/" \
    -d "{\"authentication_flow\":\"$FLOW_PK\"}"
done
```

### Set it up (declarative blueprint)

The full pattern is encoded in
[`examples/authentik-blueprint.yaml`](../examples/authentik-blueprint.yaml).
Drop it into Authentik's `/blueprints/custom/` mount and the source +
identification stage + flow + bindings + provider re-pointing are all
upserted on the next worker tick.

### Caveats

- This flow has **no fallback**: a user who lands on it can only sign in
  via the bridge. That's intentional for tenant apps but means you
  shouldn't bind it as a brand-default flow for any host that operators
  use to log into Authentik itself.
- When the IMAP server is down, end users see the bridge's error banner
  (override the strings via the bridge's `BRAND_ERROR_*` env vars). The
  bridge fails closed with a 401 to Authentik rather than 500, so
  Authentik's own error pages don't surface — the user stays on the
  bridge's branded form, sees the localized error, and can retry.
- Multiple sources on one tenant flow → Authentik shows source buttons
  instead of auto-redirecting. Keep `sources=[<one>]` to preserve the
  zero-click behavior. Want a tenant with both bridge auth and an
  external OAuth (Google etc.)? That's a different UX choice — list both
  and accept the button-page intermediate.

## 6. (Optional) Pin the bridge source to a separate Brand

If this bridge serves a tenant that should also have its own Authentik
hostname (e.g. `sso.imsteinig.de` instead of the operator's
`sso.example.com`), create a Brand for that hostname:

**System → Brands → Create**:
- Domain: `sso.imsteinig.de`
- Default flow background, default UI settings as desired.

Then on the OAuth source, restrict it to the Brand under
**Source settings → Available for**. Combined with §5's per-tenant flow,
this gives full per-tenant isolation: each tenant has its own login URL,
its own flow, its own brand.

## 7. Wire an application

Create an application (e.g. WordPress) with an OIDC provider as usual; the
fact that users sign in via the IMAP bridge is invisible to the app — they
just use Authentik like any other SSO.

To restrict the app to bridge-enrolled users, add a Group Membership policy
on the application's policy bindings:

**Applications → \<your app\> → Policy/Group/User Bindings → Add → Group Binding** → `imsteinig-members`.

## Decommissioning a user

The bridge is stateless — there is nothing on its side to remove. Two
deletes, in this order:

1. **Delete the mailbox at the IMAP server.** New `IMAP LOGIN` attempts
   start failing immediately; no fresh tokens get minted.
2. **Delete (or deactivate) the user in Authentik.** Kills any active
   session and prevents an in-flight bridge token from being accepted on
   the next `/userinfo` round-trip Authentik makes.

Existing bridge tokens stay valid until `OIDC_TOKEN_TTL` expires
(default 1 h). For a tighter window, set `OIDC_TOKEN_TTL=300` in the
bridge env — 5-minute deletion-blast-radius, modest re-auth churn.

See the README's [Decommissioning a user](../README.md#decommissioning-a-user)
section for the full rationale.

## Troubleshooting

- **HS256 / signing-key errors in Authentik logs:** the bridge always signs
  RS256 and exposes a JWKS at `/jwks.json`; if Authentik is rejecting the
  ID token signature, double-check the issuer URL matches exactly (no
  trailing slash, scheme matches).
- **Redirect URI mismatch:** Authentik shows the URI it actually used in the
  error page; copy it verbatim into `OIDC_REDIRECT_URIS`.
- **`invalid_client` from /token:** you mistyped the client secret. The
  bridge accepts both `client_secret_basic` (Authorization header) and
  `client_secret_post` (form fields).
- **Login form rejects valid credentials:** test the IMAP server directly
  with `openssl s_client -connect $IMAP_HOST:993 -crlf` then `a1 LOGIN
  user@host pass`. If that fails too, the issue is upstream.
