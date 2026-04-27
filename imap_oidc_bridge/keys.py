from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


def _b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class SigningKey:
    private: RSAPrivateKey
    kid: str

    @property
    def public(self) -> RSAPublicKey:
        return self.private.public_key()

    def jwk(self) -> dict[str, str]:
        numbers = self.public.public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _b64url_uint(numbers.n),
            "e": _b64url_uint(numbers.e),
        }

    def private_pem(self) -> bytes:
        return self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


def load_or_create(path: Path) -> SigningKey:
    """Load the RSA key from disk, generating it on first start.

    The kid is derived from the public-key SHA-256 prefix so callers can
    invalidate cached JWKS by simply rotating the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(private, RSAPrivateKey):
            raise RuntimeError(f"{path} is not an RSA private key")
    else:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(
            private.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        path.chmod(0o600)

    public_der = private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    import hashlib

    kid = hashlib.sha256(public_der).hexdigest()[:16]
    return SigningKey(private=private, kid=kid)
