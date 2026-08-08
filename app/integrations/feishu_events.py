import base64
import hashlib
import hmac
import json
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class FeishuEventError(ValueError):
    pass


def verify_event_signature(
    body: bytes,
    *,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    encrypt_key: str | None,
) -> None:
    if not encrypt_key:
        return
    if not timestamp or not nonce or not signature:
        raise FeishuEventError("missing Feishu event signature headers")
    expected = hashlib.sha256(
        timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise FeishuEventError("invalid Feishu event signature")


def decode_event_body(body: bytes, encrypt_key: str | None) -> dict[str, Any]:
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuEventError("invalid Feishu event JSON") from exc
    if not isinstance(envelope, dict):
        raise FeishuEventError("Feishu event body must be an object")
    encrypted = envelope.get("encrypt")
    if not encrypted:
        return envelope
    if not encrypt_key:
        raise FeishuEventError("APP_FEISHU_ENCRYPT_KEY is required for encrypted events")
    try:
        ciphertext = base64.b64decode(str(encrypted))
        key = hashlib.sha256(encrypt_key.encode()).digest()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(ciphertext[:16])).decryptor()
        padded = decryptor.update(ciphertext[16:]) + decryptor.finalize()
        unpadder = PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        payload = json.loads(plaintext)
    except Exception as exc:
        raise FeishuEventError("unable to decrypt Feishu event") from exc
    if not isinstance(payload, dict):
        raise FeishuEventError("decrypted Feishu event must be an object")
    return payload


def parse_text_message_event(payload: dict[str, Any]) -> dict[str, str] | None:
    header = payload.get("header") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return None
    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    if message.get("message_type") != "text" or message.get("chat_type") != "group":
        return None
    try:
        content = json.loads(message.get("content") or "{}")
    except json.JSONDecodeError as exc:
        raise FeishuEventError("invalid Feishu text message content") from exc
    result = {
        "chat_id": str(message.get("chat_id") or ""),
        "message_id": str(message.get("message_id") or ""),
        "author_id": str(sender_id.get("open_id") or ""),
        "text": str(content.get("text") or "").strip(),
    }
    if not all(result.values()):
        raise FeishuEventError("Feishu message event omitted required fields")
    return result
