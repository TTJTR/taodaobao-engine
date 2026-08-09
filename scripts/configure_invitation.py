"""Enable, rotate, inspect, or disable the deployment invitation gate safely."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from configure_feishu_runtime import parse_env, render_env, write_atomically


def write_secret(path: Path, signing_secret: str) -> None:
    write_atomically(path, signing_secret + "\n")
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path("/opt/taodaobao/runtime.env")
    )
    parser.add_argument(
        "--secret-output",
        type=Path,
        default=Path("/root/taodaobao-invitation-signing-secret.txt"),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable", action="store_true", help="enable and rotate the code")
    action.add_argument("--disable", action="store_true", help="disable the invitation gate")
    action.add_argument("--check", action="store_true", help="show status without the code")
    args = parser.parse_args()

    original = args.env_file.read_text(encoding="utf-8") if args.env_file.exists() else ""
    current = parse_env(original)
    if args.check:
        enabled = current.get("APP_INVITATION_REQUIRED", "false").lower() == "true"
        configured = bool(
            current.get("APP_INVITATION_SIGNING_SECRET") or current.get("APP_INVITATION_CODE")
        )
        print(f"Invitation gate: {'enabled' if enabled else 'disabled'}")
        print(f"Invitation signer: {'configured' if configured else 'missing'} (value hidden)")
        return 0 if not enabled or configured else 1

    if args.disable:
        updates = {
            "APP_INVITATION_REQUIRED": "false",
            "APP_INVITATION_CODE": "",
            "APP_INVITATION_SIGNING_SECRET": "",
        }
        write_atomically(args.env_file, render_env(original, updates))
        print("Invitation gate disabled; previous code removed from the runtime environment.")
        return 0

    signing_secret = secrets.token_urlsafe(48)
    updates = {
        "APP_INVITATION_REQUIRED": "true",
        "APP_INVITATION_CODE": "",
        "APP_INVITATION_SIGNING_SECRET": signing_secret,
        "APP_INVITATION_TTL_SECONDS": "600",
        "APP_INVITATION_MAX_TOKEN_TTL_SECONDS": "2592000",
    }
    write_atomically(args.env_file, render_env(original, updates))
    write_secret(args.secret_output, signing_secret)
    print("Invitation gate enabled and the signing secret was rotated (value hidden).")
    print(f"Retrieve it from {args.secret_output}; file permissions are 0600.")
    print("Use scripts/generate_invitation.py on a trusted computer to create invitations.")
    print("Next: recreate the application container so it reloads the env file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
