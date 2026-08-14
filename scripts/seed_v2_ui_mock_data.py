"""Retired: full-business Mock seeding is intentionally disabled."""


def main() -> int:
    print(
        "[SKIPPED] Full V2 UI Mock seeding is disabled. "
        "Run scripts/verify_builtin_document_index.py for synthetic document indexing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
