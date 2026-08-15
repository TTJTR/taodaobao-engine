"""Signed, offline-generatable invitation tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

TOKEN_PREFIX = "tdb1"
TOKEN_AUDIENCE = "taodaobao"
SHORT_CODE_PREFIX = "tdb-"
SHORT_CODE_LENGTH = 5
SHORT_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SHORT_CODE_PATTERN = re.compile(r"^tdb-[a-hj-km-np-z2-9]{5}$", re.IGNORECASE)


@dataclass(frozen=True)
class InvitationClaims:
    token_id: str
    issued_at: int
    expires_at: int
    max_uses: int = 1

    @property
    def token_id_hash(self) -> str:
        return hashlib.sha256(self.token_id.encode()).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key(secret: str) -> bytes:
    return hashlib.sha256(("taodaobao-invitation-v1:" + secret).encode()).digest()


def generate_short_invitation_code() -> str:
    """Return a memorable server-registered code such as ``tdb-k7m2q``."""
    suffix = "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH))
    return f"{SHORT_CODE_PREFIX}{suffix}"


def normalize_short_invitation_code(code: str) -> str | None:
    normalized = code.strip().lower()
    return normalized if SHORT_CODE_PATTERN.fullmatch(normalized) else None


def hash_short_invitation_code(code: str, secret: str) -> str | None:
    """Hash a short code with the deployment secret before database lookup."""
    normalized = normalize_short_invitation_code(code)
    if normalized is None or len(secret) < 32:
        return None
    return hmac.new(
        _key(secret),
        f"taodaobao-short-invitation-v1:{normalized}".encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_invitation_token(
    secret: str,
    *,
    ttl_seconds: int,
    max_uses: int = 1,
    now: int | None = None,
) -> str:
    if len(secret) < 32:
        raise ValueError("invitation signing secret must contain at least 32 characters")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if not 1 <= max_uses <= 100:
        raise ValueError("max_uses must be between 1 and 100")
    issued_at = int(time.time()) if now is None else now
    payload = {
        "aud": TOKEN_AUDIENCE,
        "exp": issued_at + ttl_seconds,
        "iat": issued_at,
        "jti": secrets.token_urlsafe(16),
        "uses": max_uses,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signed = f"{TOKEN_PREFIX}.{encoded}"
    signature = _encode(hmac.new(_key(secret), signed.encode(), hashlib.sha256).digest())
    return f"{signed}.{signature}"


def verify_invitation_token(
    token: str,
    secret: str,
    *,
    max_ttl_seconds: int,
    now: int | None = None,
) -> InvitationClaims | None:
    try:
        prefix, encoded, signature = token.split(".")
        if prefix != TOKEN_PREFIX or len(secret) < 32:
            return None
        expected = _encode(
            hmac.new(
                _key(secret), f"{prefix}.{encoded}".encode(), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_decode(encoded))
        claims = InvitationClaims(
            token_id=str(payload["jti"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
            max_uses=int(payload.get("uses", 1)),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    current = int(time.time()) if now is None else now
    if payload.get("aud") != TOKEN_AUDIENCE or not claims.token_id:
        return None
    if claims.issued_at > current + 60 or claims.expires_at < current:
        return None
    if not 1 <= claims.max_uses <= 100:
        return None
    invalid_ttl = claims.expires_at - claims.issued_at > max_ttl_seconds
    if claims.expires_at <= claims.issued_at or invalid_ttl:
        return None
    return claims
