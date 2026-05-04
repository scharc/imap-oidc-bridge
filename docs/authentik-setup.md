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

## 5. Add the source to a Brand (optional)

If this bridge serves a separate tenant (e.g. `sso.imsteinig.de`), create a
Brand for that hostname and pin the source to it so users on the main brand
don't see it on their login page.

**System → Brands → Create**:
- Domain: `sso.imsteinig.de`
- Default flow background, default UI settings as desired.

Then on the OAuth source, restrict it to the Brand under
**Source settings → Available for**.

## 6. Wire an application

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
