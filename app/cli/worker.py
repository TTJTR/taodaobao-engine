import argparse
import asyncio

from app.core.logging import configure_logging
from app.services.durable_worker import run_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the recoverable workflow worker")
    parser.add_argument("--once", action="store_true", help="Claim at most one task")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    configure_logging()
    asyncio.run(run_worker(poll_seconds=args.poll_seconds, once=args.once))


if __name__ == "__main__":
    main()
