import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.presentation.assets import AssetCatalog  # noqa: E402


def main() -> int:
    catalog = AssetCatalog()
    errors = catalog.validate()
    manifest = catalog.load()
    runtime = catalog.runtime_assets()
    print(f"assets={len(manifest.assets)} runtime_eligible={len(runtime)} errors={len(errors)}")
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
