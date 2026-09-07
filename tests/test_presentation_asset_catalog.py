import json
import shutil
from pathlib import Path

from app.presentation.assets import AssetCatalog


def test_checked_in_asset_catalog_is_complete_and_safe() -> None:
    catalog = AssetCatalog()

    assert catalog.validate() == []
    assert len(catalog.runtime_assets("template")) == 3
    assert not any(asset.asset_type == "brand" for asset in catalog.runtime_assets())


def test_asset_hashes_are_stable_with_linux_line_endings(tmp_path: Path) -> None:
    source = AssetCatalog().root
    root = tmp_path / "catalog"
    shutil.copytree(source, root)
    manifest = AssetCatalog(root).load()

    for asset in manifest.assets:
        if asset.mime_type.startswith("text/") or asset.mime_type in {
            "application/json",
            "image/svg+xml",
        }:
            path = root / asset.local_path
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))

    assert AssetCatalog(root).validate() == []


def test_catalog_rejects_unconfirmed_brand_asset_from_runtime(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (root / "license.txt").write_text("brand permission pending", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "presentation-assets-v1",
                "generated_at": "2026-08-11T00:00:00+08:00",
                "assets": [
                    {
                        "asset_id": "brand.digitalchina.logo",
                        "asset_type": "brand",
                        "local_path": "logo.svg",
                        "source_url": "https://example.invalid/logo.svg",
                        "upstream_project": "Digital China",
                        "upstream_revision": "unconfirmed",
                        "license": "brand-owner-permission-pending",
                        "license_path": "license.txt",
                        "sha256": (
                            "bf21a9e8fbc5a3846fb05b4fa0859e0917b2202f4f7f40c2f4efcc329f04ff4e"
                        ),
                        "mime_type": "image/svg+xml",
                        "purpose": "logo",
                        "commercial_status": "allowed",
                        "brand_confirmation_status": "pending_brand_confirmation",
                        "runtime_eligible": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        AssetCatalog(root).load()
    except ValueError as exc:
        assert "unconfirmed brand assets" in str(exc)
    else:
        raise AssertionError("unconfirmed brand asset entered runtime")
