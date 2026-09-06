"""Safely configure Feishu live-mode environment variables on a server."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SCOPES = (
    "offline_access docx:document space:document:retrieve "
    "minutes:minutes.search:read minutes:minutes.basic:read "
    "minutes:minutes.transcript:export"
)
REQUIRED_LIVE_KEYS = (
    "APP_PUBLIC_BASE_URL",
    "APP_FEISHU_APP_ID",
    "APP_FEISHU_APP_SECRET",
    "APP_FEISHU_VERIFICATION_TOKEN",
    "APP_FEISHU_ENCRYPT_KEY",
    "APP_FEISHU_TOKEN_ENCRYPTION_KEY",
)


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def render_env(original: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    rendered: list[str] = []
    for raw_line in original.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                rendered.append(f"{key}={remaining.pop(key)}")
                continue
        rendered.append(raw_line)
    if remaining:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(rendered).rstrip() + "\n"


def generate_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def validate_live_config(values: dict[str, str]) -> list[str]:
    missing = [key for key in REQUIRED_LIVE_KEYS if not values.get(key)]
    base_url = values.get("APP_PUBLIC_BASE_URL", "")
    if base_url and not base_url.startswith(("http://", "https://")):
        missing.append("APP_PUBLIC_BASE_URL (must start with http:// or https://)")
    app_id = values.get("APP_FEISHU_APP_ID", "")
    if app_id and not app_id.startswith("cli_"):
        missing.append("APP_FEISHU_APP_ID (expected cli_ prefix)")
    return missing


def write_atomically(path: Path, content: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        # The host OS currently uses Python 3.10, where datetime.UTC is unavailable.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
        backup = path.with_name(f"{path.name}.bak.{timestamp}")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)
    return backup


def prompt_value(label: str, current: str, *, secret: bool = False) -> str:
    suffix = " [留空则保留现有值]" if current else ""
    prompt = f"{label}{suffix}: "
    entered = getpass.getpass(prompt) if secret else input(prompt).strip()
    return entered or current


def collect_updates(current: dict[str, str], default_base_url: str) -> dict[str, str]:
    base_url = prompt_value(
        "公网访问地址（例如 https://sales.example.com）",
        current.get("APP_PUBLIC_BASE_URL", default_base_url),
    ).rstrip("/")
    app_id = prompt_value("飞书 App ID", current.get("APP_FEISHU_APP_ID", ""))
    app_secret = prompt_value(
        "飞书 App Secret", current.get("APP_FEISHU_APP_SECRET", ""), secret=True
    )
    verification_token = prompt_value(
        "飞书 Verification Token",
        current.get("APP_FEISHU_VERIFICATION_TOKEN", ""),
        secret=True,
    )
    encrypt_key = prompt_value(
        "飞书 Encrypt Key", current.get("APP_FEISHU_ENCRYPT_KEY", ""), secret=True
    )
    token_key = current.get("APP_FEISHU_TOKEN_ENCRYPTION_KEY") or generate_fernet_key()
    scopes = prompt_value("OAuth scopes", current.get("APP_FEISHU_SCOPES", DEFAULT_SCOPES))
    is_https = base_url.startswith("https://")
    return {
        "APP_FEISHU_MODE": "live",
        "APP_PUBLIC_BASE_URL": base_url,
        "APP_FRONTEND_REDIRECT_URL": f"{base_url}/",
        "APP_COOKIE_SECURE": "true" if is_https else "false",
        "APP_FEISHU_APP_ID": app_id,
        "APP_FEISHU_APP_SECRET": app_secret,
        "APP_FEISHU_VERIFICATION_TOKEN": verification_token,
        "APP_FEISHU_ENCRYPT_KEY": encrypt_key,
        "APP_FEISHU_TOKEN_ENCRYPTION_KEY": token_key,
        "APP_FEISHU_SCOPES": scopes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path("/opt/taodaobao/runtime.env")
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("APP_PUBLIC_BASE_URL", "http://127.0.0.1:3000"),
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    original = args.env_file.read_text(encoding="utf-8") if args.env_file.exists() else ""
    current = parse_env(original)
    if args.check_only:
        missing = validate_live_config(current)
        mode = current.get("APP_FEISHU_MODE", "not_set")
        print(f"Feishu mode: {mode}")
        if missing:
            print("Live configuration incomplete: " + ", ".join(missing))
            return 1
        print("Live configuration fields are complete (secret values hidden).")
        return 0

    updates = collect_updates(current, args.base_url)
    missing = validate_live_config(updates)
    if missing:
        print("Configuration not written: " + ", ".join(missing))
        return 2
    backup = write_atomically(args.env_file, render_env(original, updates))
    print(f"Updated {args.env_file} with permissions 0600; secret values were not displayed.")
    if backup:
        print(f"Backup: {backup}")
    print("Next: recreate the application container so it reloads the env file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
