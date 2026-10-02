"""Small, dependency-free OIDC JWT protocol adapter.

The adapter deliberately supports the RSA JWT profile used by the fixture and
by common OIDC providers (RS256).  It does not discover or trust arbitrary
issuer metadata.  Production configuration must provide an exact issuer,
audience and a pinned JWKS document or a configured JWKS URL.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx


class IdentityVerificationError(ValueError):
    """A token cannot be trusted under the configured OIDC policy."""


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise IdentityVerificationError("malformed base64 value") from exc


def _json_part(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(_b64decode(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityVerificationError("malformed JWT JSON") from exc
    if not isinstance(parsed, dict):
        raise IdentityVerificationError("JWT part must be an object")
    return parsed


def _audience_matches(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    return isinstance(value, list) and expected in value and all(isinstance(item, str) for item in value)


def _rsa_verify_sha256(message: bytes, signature: bytes, modulus: int, exponent: int) -> bool:
    """Verify RS256 without an optional crypto package.

    The encoded message is checked byte-for-byte against the SHA-256 PKCS#1
    v1.5 DigestInfo.  This avoids accepting a weaker padding variant.
    """

    key_bytes = (modulus.bit_length() + 7) // 8
    if key_bytes < 256 or len(signature) != key_bytes:
        return False
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(key_bytes, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (key_bytes - len(digest_info) - 3) + b"\x00" + digest_info
    return encoded == expected


@dataclass(frozen=True)
class OIDCConfig:
    mode: str
    issuer: str | None
    audience: str | None
    jwks_json: str | None
    jwks_url: str | None
    allowed_algorithms: frozenset[str]
    clock_skew_seconds: int = 30

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        mode = os.getenv("AUTH_MODE", "demo").strip().casefold()
        if mode not in {"demo", "test", "production"}:
            raise IdentityVerificationError("AUTH_MODE must be demo, test, or production")
        algorithms = frozenset(
            value.strip().upper()
            for value in os.getenv("OIDC_ALLOWED_ALGORITHMS", "RS256").split(",")
            if value.strip()
        )
        try:
            skew = int(os.getenv("OIDC_CLOCK_SKEW_SECONDS", "30"))
        except ValueError as exc:
            raise IdentityVerificationError("OIDC_CLOCK_SKEW_SECONDS must be an integer") from exc
        if skew < 0 or skew > 300:
            raise IdentityVerificationError("OIDC_CLOCK_SKEW_SECONDS must be between 0 and 300")
        return cls(
            mode=mode,
            issuer=os.getenv("OIDC_ISSUER", "").strip() or None,
            audience=os.getenv("OIDC_AUDIENCE", "").strip() or None,
            jwks_json=os.getenv("OIDC_JWKS_JSON", "").strip() or None,
            jwks_url=os.getenv("OIDC_JWKS_URL", "").strip() or None,
            allowed_algorithms=algorithms,
            clock_skew_seconds=skew,
        )

    def validate_for_verification(self) -> None:
        if not self.issuer or not self.audience:
            raise IdentityVerificationError("OIDC issuer and audience are required")
        parsed_issuer = urlparse(self.issuer)
        if parsed_issuer.scheme != "https" or not parsed_issuer.netloc or parsed_issuer.username or parsed_issuer.password:
            raise IdentityVerificationError("OIDC issuer must be an https URL")
        if not self.allowed_algorithms or not self.allowed_algorithms.issubset({"RS256"}):
            raise IdentityVerificationError("only explicitly supported RS256 is allowed")
        if bool(self.jwks_json) == bool(self.jwks_url):
            raise IdentityVerificationError("configure exactly one OIDC JWKS source")
        if self.jwks_url:
            parsed_jwks = urlparse(self.jwks_url)
            if parsed_jwks.scheme != "https" or not parsed_jwks.netloc or parsed_jwks.username or parsed_jwks.password:
                raise IdentityVerificationError("OIDC JWKS URL must be an https URL")


def _jwks(config: OIDCConfig) -> list[dict[str, Any]]:
    if config.jwks_json:
        try:
            value = json.loads(config.jwks_json)
        except json.JSONDecodeError as exc:
            raise IdentityVerificationError("OIDC_JWKS_JSON is invalid JSON") from exc
    else:
        try:
            response = httpx.get(config.jwks_url or "", timeout=5.0, follow_redirects=False)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityVerificationError("unable to load OIDC JWKS") from exc
    keys = value.get("keys") if isinstance(value, dict) else None
    if not isinstance(keys, list):
        raise IdentityVerificationError("OIDC JWKS must contain a keys array")
    return [key for key in keys if isinstance(key, dict)]


def verify_bearer_token(token: str, config: OIDCConfig | None = None, *, now: int | None = None) -> str:
    """Validate a Bearer JWT and return its trusted ``sub`` claim."""

    config = config or OIDCConfig.from_env()
    config.validate_for_verification()
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise IdentityVerificationError("malformed bearer token")
    header = _json_part(parts[0])
    claims = _json_part(parts[1])
    algorithm = header.get("alg")
    if algorithm not in config.allowed_algorithms or algorithm != "RS256":
        raise IdentityVerificationError("JWT algorithm is not allowed")
    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        raise IdentityVerificationError("JWT key id is required")
    keys = [
        key
        for key in _jwks(config)
        if key.get("kid") == key_id and key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256"
    ]
    if len(keys) != 1:
        raise IdentityVerificationError("JWT signing key is not trusted")
    key = keys[0]
    try:
        modulus = int.from_bytes(_b64decode(str(key["n"])), "big")
        exponent = int.from_bytes(_b64decode(str(key["e"])), "big")
    except (KeyError, IdentityVerificationError) as exc:
        raise IdentityVerificationError("invalid RSA JWK") from exc
    if not _rsa_verify_sha256(f"{parts[0]}.{parts[1]}".encode("ascii"), _b64decode(parts[2]), modulus, exponent):
        raise IdentityVerificationError("JWT signature is invalid")

    if claims.get("iss") != config.issuer:
        raise IdentityVerificationError("JWT issuer is invalid")
    if not _audience_matches(claims.get("aud"), config.audience or ""):
        raise IdentityVerificationError("JWT audience is invalid")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise IdentityVerificationError("JWT subject is invalid")
    timestamp = int(time.time() if now is None else now)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or timestamp >= exp + config.clock_skew_seconds:
        raise IdentityVerificationError("JWT is expired")
    nbf = claims.get("nbf")
    if nbf is not None and (not isinstance(nbf, (int, float)) or isinstance(nbf, bool) or timestamp + config.clock_skew_seconds < nbf):
        raise IdentityVerificationError("JWT is not active")
    return subject


def bearer_from_headers(headers: Mapping[str, str]) -> str | None:
    value = next((value for name, value in headers.items() if name.casefold() == "authorization"), None)
    if value is None:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise IdentityVerificationError("Authorization must be a Bearer token")
    return token.strip()
