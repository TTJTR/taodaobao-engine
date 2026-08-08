"""Enable, rotate, inspect, or disable the deployment invitation gate safely."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from configure_feishu_runtime import parse_env, render_env, write_atomically


def generate_invitation_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)]
    return "TAO-" + "-".join(groups)


def write_secret(path: Path, invitation_code: str) -> None:
    write_atomically(path, invitation_code + "\n")
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path("/opt/taodaobao/runtime.env")
    )
    parser.add_argument(
        "--secret-output", type=Path, default=Path("/root/taodaobao-invitation.txt")
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
        configured = bool(current.get("APP_INVITATION_CODE"))
        print(f"Invitation gate: {'enabled' if enabled else 'disabled'}")
        print(f"Invitation code: {'configured' if configured else 'missing'} (value hidden)")
        return 0 if not enabled or configured else 1

    if args.disable:
        updates = {"APP_INVITATION_REQUIRED": "false", "APP_INVITATION_CODE": ""}
        write_atomically(args.env_file, render_env(original, updates))
        print("Invitation gate disabled; previous code removed from the runtime environment.")
        return 0

    invitation_code = generate_invitation_code()
    updates = {
        "APP_INVITATION_REQUIRED": "true",
        "APP_INVITATION_CODE": invitation_code,
        "APP_INVITATION_TTL_SECONDS": "600",
    }
    write_atomically(args.env_file, render_env(original, updates))
    write_secret(args.secret_output, invitation_code)
    print("Invitation gate enabled and the code was rotated (value hidden).")
    print(f"Retrieve it from {args.secret_output}; file permissions are 0600.")
    print("Next: recreate the application container so it reloads the env file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
