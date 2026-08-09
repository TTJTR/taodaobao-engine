"""Generate signed, time-limited Taodaobao invitation tokens offline."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.core.invitations import generate_invitation_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=24, help="validity in hours (default: 24)")
    parser.add_argument("--count", type=int, default=1, help="number of single-use tokens")
    args = parser.parse_args()
    if not 1 <= args.hours <= 720:
        parser.error("--hours must be between 1 and 720")
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")
    secret = args.secret_file.read_text(encoding="utf-8").strip()
    for _ in range(args.count):
        print(generate_invitation_token(secret, ttl_seconds=args.hours * 3600))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
