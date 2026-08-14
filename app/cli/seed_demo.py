"""Load synthetic built-in documents through the real indexing verifier."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from scripts.verify_builtin_document_index import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="retained for CLI compatibility; fixture upsert is already idempotent",
    )
    parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                Path("fixtures/builtin_documents/index_cases.json"),
                Path(".local/builtin-index-report.json"),
            )
        )
    )


if __name__ == "__main__":
    main()
