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

    @property
    def issuer(self) -> str:
        return str(self.oidc_issuer).rstrip("/")
