from __future__ import annotations

import time
from typing import Any

from authlib.jose import jwt

from .config import Settings
from .keys import SigningKey


def discovery_document(settings: Settings) -> dict[str, Any]:
    issuer = settings.issuer
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "userinfo_endpoint": f"{issuer}/userinfo",
        "jwks_uri": f"{issuer}/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": settings.oidc_scopes_supported,
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
        "claims_supported": ["sub", "email", "email_verified", "iss", "aud", "exp", "iat"],
        "grant_types_supported": ["authorization_code"],
    }


def jwks_document(key: SigningKey) -> dict[str, Any]:
    return {"keys": [key.jwk()]}


def mint_id_token(
    *,
    settings: Settings,
    key: SigningKey,
    email: str,
    nonce: str | None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": settings.issuer,
        "sub": email,
        "aud": settings.oidc_client_id,
        "iat": now,
        "exp": now + settings.oidc_token_ttl,
        "email": email,
        "email_verified": True,
    }
    if nonce:
        claims["nonce"] = nonce
    header = {"alg": "RS256", "kid": key.kid, "typ": "JWT"}
    token = jwt.encode(header, claims, key.private_pem())
    return token.decode("ascii") if isinstance(token, bytes) else token
