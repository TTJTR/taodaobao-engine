import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass


class InvalidSessionError(ValueError):
    """Raised when a session token is malformed, forged, or expired."""


@dataclass(frozen=True, slots=True)
class SessionClaims:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    expires_at: int


class SessionCodec:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        if len(secret) < 16:
            raise ValueError("session secret must contain at least 16 characters")
        if ttl_seconds <= 0:
            raise ValueError("session TTL must be positive")
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _encode_base64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_base64(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def encode(self, user_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
        payload = {
            "exp": int(time.time()) + self._ttl_seconds,
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
        }
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        encoded_payload = self._encode_base64(serialized)
        signature = hmac.new(
            self._secret, encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        return f"{encoded_payload}.{self._encode_base64(signature)}"

    def decode(self, token: str) -> SessionClaims:
        try:
            encoded_payload, encoded_signature = token.split(".", maxsplit=1)
            expected_signature = hmac.new(
                self._secret, encoded_payload.encode("ascii"), hashlib.sha256
            ).digest()
            actual_signature = self._decode_base64(encoded_signature)
            if not hmac.compare_digest(actual_signature, expected_signature):
                raise InvalidSessionError("invalid session signature")
            payload = json.loads(self._decode_base64(encoded_payload))
            claims = SessionClaims(
                user_id=uuid.UUID(payload["user_id"]),
                workspace_id=uuid.UUID(payload["workspace_id"]),
                expires_at=int(payload["exp"]),
            )
        except InvalidSessionError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidSessionError("malformed session token") from exc

        if claims.expires_at < int(time.time()):
            raise InvalidSessionError("session token has expired")
        return claims
