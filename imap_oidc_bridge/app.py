from __future__ import annotations

import base64
import logging
import secrets
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import structlog
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import __version__
from .config import Settings
from .imap import IMAPAuthError, IMAPBackend
from .keys import SigningKey, load_or_create
from .oidc import discovery_document, jwks_document, mint_id_token
from .store import CodeStore

PENDING_COOKIE = "imap_oidc_pending"
PENDING_TTL = 600  # 10 min — covers slow typists


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    log = structlog.get_logger("imap_oidc_bridge")

    key: SigningKey = load_or_create(Path(settings.data_dir) / "signing.pem")
    codes = CodeStore(ttl=settings.oidc_code_ttl)
    imap = IMAPBackend(
        host=settings.imap_host,
        port=settings.imap_port,
        ssl=settings.imap_ssl,
        timeout=settings.imap_timeout,
    )
    serializer = URLSafeTimedSerializer(settings.session_secret, salt="imap-oidc-pending")

    templates = Jinja2Templates(
        directory=str(Path(__file__).parent / "templates"),
    )

    app = FastAPI(title="imap-oidc-bridge", version=__version__)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> JSONResponse:
        return JSONResponse(discovery_document(settings))

    @app.get("/jwks.json")
    async def jwks() -> JSONResponse:
        return JSONResponse(jwks_document(key))

    def _validate_redirect(uri: str) -> None:
        if uri not in settings.oidc_redirect_uris:
            raise HTTPException(400, "redirect_uri not registered")

    @app.get("/authorize")
    async def authorize(
        request: Request,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        scope: str = "openid",
        state: str | None = None,
        nonce: str | None = None,
    ) -> Response:
        if response_type != "code":
            raise HTTPException(400, "unsupported_response_type")
        if client_id != settings.oidc_client_id:
            raise HTTPException(400, "unknown client_id")
        _validate_redirect(redirect_uri)
        if "openid" not in scope.split():
            raise HTTPException(400, "openid scope required")

        pending = serializer.dumps(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope,
                "state": state,
                "nonce": nonce,
            }
        )
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": "Sign in",
                "form_action": "/authorize",
                "request_id": pending,
                "error": None,
                "email": "",
            },
        )

    @app.post("/authorize")
    async def authorize_submit(
        request: Request,
        request_id: Annotated[str, Form()],
        email: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        try:
            pending = serializer.loads(request_id, max_age=PENDING_TTL)
        except BadSignature as exc:
            log.warning("authorize.bad_request_id", error=str(exc))
            raise HTTPException(400, "invalid request") from exc

        try:
            imap.verify(email, password)
        except IMAPAuthError as exc:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "title": "Sign in",
                    "form_action": "/authorize",
                    "request_id": request_id,
                    "error": str(exc),
                    "email": email,
                },
                status_code=401,
            )

        entry = codes.issue(
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            email=email,
            scope=pending["scope"],
            nonce=pending["nonce"],
            state=pending["state"],
        )

        params: dict[str, str] = {"code": entry.code}
        if entry.state:
            params["state"] = entry.state
        target = f"{entry.redirect_uri}?{urlencode(params)}"
        return RedirectResponse(target, status_code=303)

    def _client_auth(request_headers: dict[str, str], form: dict[str, str]) -> None:
        header = request_headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
                cid, _, csec = decoded.partition(":")
            except Exception as exc:
                raise HTTPException(401, "invalid client auth") from exc
        else:
            cid = form.get("client_id", "")
            csec = form.get("client_secret", "")
        if not (
            secrets.compare_digest(cid, settings.oidc_client_id)
            and secrets.compare_digest(csec, settings.oidc_client_secret)
        ):
            raise HTTPException(401, "invalid_client")

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        raw_form = await request.form()
        form: dict[str, str] = {k: v for k, v in raw_form.items() if isinstance(v, str)}
        headers = {k.lower(): v for k, v in request.headers.items()}
        _client_auth(headers, form)

        if form.get("grant_type") != "authorization_code":
            raise HTTPException(400, "unsupported_grant_type")
        code = form.get("code")
        redirect_uri = form.get("redirect_uri", "")
        if not code:
            raise HTTPException(400, "missing code")

        entry = codes.consume(code)
        if entry is None:
            raise HTTPException(400, "invalid or expired code")
        if entry.redirect_uri != redirect_uri:
            raise HTTPException(400, "redirect_uri mismatch")

        id_token = mint_id_token(
            settings=settings,
            key=key,
            email=entry.email,
            nonce=entry.nonce,
        )
        # Access token equals id_token here; the only protected resource is /userinfo
        # which re-derives identity from the same JWT.
        return JSONResponse(
            {
                "access_token": id_token,
                "id_token": id_token,
                "token_type": "Bearer",
                "expires_in": settings.oidc_token_ttl,
                "scope": entry.scope,
            }
        )

    @app.get("/userinfo")
    async def userinfo(request: Request) -> JSONResponse:
        from authlib.jose import JsonWebKey
        from authlib.jose import jwt as jose_jwt
        from authlib.jose.errors import JoseError

        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        token = header.split(" ", 1)[1]
        try:
            jwk = JsonWebKey.import_key(key.jwk())
            claims = jose_jwt.decode(token, jwk)
            claims.validate()
        except JoseError as exc:
            raise HTTPException(401, "invalid token") from exc

        return JSONResponse(
            {
                "sub": claims["sub"],
                "email": claims["email"],
                "email_verified": True,
            }
        )

    return app
