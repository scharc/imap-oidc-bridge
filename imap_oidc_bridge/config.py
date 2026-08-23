from __future__ import annotations

from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    imap_host: str = Field(..., description="IMAP server hostname")
    imap_port: int = Field(993, description="IMAP server port")
    imap_ssl: bool = Field(True, description="Use IMAP4 over SSL")
    imap_timeout: float = Field(10.0, description="IMAP connect/login timeout in seconds")

    oidc_issuer: HttpUrl = Field(..., description="Public OIDC issuer URL, no trailing slash")
    oidc_client_id: str = Field(..., description="Single OIDC client identifier")
    oidc_client_secret: str = Field(..., description="Single OIDC client secret")
    oidc_redirect_uris: list[str] = Field(
        ..., description="Allowed redirect URIs for the client (comma-separated env)"
    )
    oidc_code_ttl: int = Field(60, description="Authorization code TTL in seconds")
    oidc_token_ttl: int = Field(3600, description="ID/access token TTL in seconds")
    oidc_scopes_supported: list[str] = Field(
        default_factory=lambda: ["openid", "email", "profile"],
        description="Scopes advertised in discovery; the bridge only ever has email available.",
    )

    data_dir: Path = Field(Path("/data"), description="Where the RSA signing key is persisted")
    session_secret: str = Field(
        ..., description="Secret used for short-lived signed cookies during the auth flow"
    )

    log_level: str = Field("INFO", description="Python log level")
    bind_host: str = Field("0.0.0.0", description="Uvicorn bind host")
    bind_port: int = Field(8000, description="Uvicorn bind port")

    # ── Branding (login form) ──────────────────────────────────────────────
    # The form ships with neutral defaults and pulls no external assets unless
    # one of these is set. Everything is optional. URL fields render as-is in
    # `<img>` tags; HTML slots render verbatim (operator-trusted content only).
    brand_logo_url: str | None = Field(
        None,
        description="URL of an SVG/PNG shown above the login form (a wordmark).",
    )
    brand_title: str = Field(
        "Sign in",
        description="Heading shown on the login form.",
    )
    brand_subtext: str = Field(
        "Sign in with your mailbox credentials.",
        description="Hint text shown under the heading.",
    )
    brand_header_html: str | None = Field(
        None,
        description="Free HTML rendered above the title (banner, welcome message).",
    )
    brand_below_form_html: str | None = Field(
        None,
        description="Free HTML rendered between the submit button and the footer.",
    )
    brand_footer_html: str | None = Field(
        None,
        description="Free HTML in the footer (replaces the default IMAP-trust line).",
    )
    brand_footer_links: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "List of {label, href} pairs rendered as a centered ·-separated row "
            "in the footer. JSON-encoded in env: "
            '\'[{"label": "Impressum", "href": "https://example.com/impressum"}]\''
        ),
    )
    brand_accent_color: str | None = Field(
        None,
        description="CSS color for the submit button + focus ring (e.g. '#7c3aed').",
    )
    brand_background_url: str | None = Field(
        None,
        description="Optional full-page background image URL.",
    )

    # ── Root landing page (GET /) ─────────────────────────────────────────
    # Served when someone opens the bare bridge host directly instead of
    # arriving via an app's OIDC redirect. Reveals nothing about the service;
    # just a friendly, on-brand nudge back to signing in.
    brand_landing_html: str | None = Field(
        None,
        description="Free HTML body for the landing page. Falls back to brand_subtext.",
    )
    brand_home_url: str | None = Field(
        None,
        description="If set, the landing page shows a button linking here (the tenant's main app/portal).",
    )
    brand_home_label: str = Field(
        "Continue",
        description="Label of the landing-page button (only shown when brand_home_url is set).",
    )

    # Per-string overrides — lighter than full i18n. Operators who want German
    # set BRAND_LABEL_EMAIL=E-Mail etc. without dragging in a translation
    # framework. Defaults stay English.
    brand_lang: str = Field(
        "en",
        description="Sets the <html lang> attribute (BCP 47, e.g. 'de').",
    )
    brand_label_email: str = Field("Email")
    brand_label_password: str = Field("Password")
    brand_label_submit: str = Field("Sign in")
    brand_label_submitting: str = Field("Signing in…")
    brand_placeholder_email: str = Field("you@example.com")
    brand_aria_footer_links: str = Field(
        "Site links",
        description="ARIA label for the footer-links nav (screen-reader text).",
    )

    # Error messages shown in the red banner under the form. Mapped from
    # IMAPAuthError.code → BRAND_ERROR_<CODE>.
    brand_error_empty_credentials: str = Field("Email and password are required.")
    brand_error_invalid_credentials: str = Field("Invalid email or password.")
    brand_error_upstream_unreachable: str = Field(
        "Mail server is currently unreachable. Please try again."
    )
    brand_error_upstream_error: str = Field("Mail server returned an error.")

    def localized_error(self, code: str, fallback: str) -> str:
        """Return the operator-overridden error string for an IMAPAuthError code."""
        attr = f"brand_error_{code}"
        return getattr(self, attr, None) or fallback

    @property
    def issuer(self) -> str:
        return str(self.oidc_issuer).rstrip("/")
