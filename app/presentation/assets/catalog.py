from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from defusedxml import ElementTree as SafeElementTree
from pydantic import BaseModel, ConfigDict, Field, model_validator

AssetType = Literal["template", "thumbnail", "decoration", "font", "brand", "license"]
CommercialStatus = Literal["allowed", "review_required", "prohibited"]
BrandStatus = Literal["not_applicable", "confirmed", "pending_brand_confirmation"]


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    asset_type: AssetType
    local_path: str
    source_url: str
    upstream_project: str
    upstream_revision: str
    license: str
    license_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str
    purpose: str
    commercial_status: CommercialStatus
    brand_confirmation_status: BrandStatus = "not_applicable"
    runtime_eligible: bool = False
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def enforce_release_gate(self) -> AssetRecord:
        if self.runtime_eligible and self.commercial_status != "allowed":
            raise ValueError("runtime assets must be commercially allowed")
        if self.runtime_eligible and self.brand_confirmation_status == "pending_brand_confirmation":
            raise ValueError("unconfirmed brand assets cannot be runtime eligible")
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be supplied together")
        return self


class AssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["presentation-assets-v1"]
    generated_at: str
    assets: tuple[AssetRecord, ...]

    @model_validator(mode="after")
    def unique_ids_and_paths(self) -> AssetManifest:
        ids = [asset.asset_id for asset in self.assets]
        paths = [asset.local_path for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset_id must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("local_path must be unique")
        return self


class AssetCatalog:
    """Read and verify the immutable, provenance-aware asset manifest."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[2] / "assets" / "presentation"
        self.manifest_path = self.root / "manifest.json"

    def load(self) -> AssetManifest:
        return AssetManifest.model_validate_json(self.manifest_path.read_text(encoding="utf-8"))

    def validate(self) -> list[str]:
        manifest = self.load()
        errors: list[str] = []
        hashes: dict[str, list[str]] = {}
        for asset in manifest.assets:
            path = self._resolve(asset.local_path)
            license_path = self._resolve(asset.license_path)
            if not path.is_file():
                errors.append(f"MISSING_FILE:{asset.asset_id}")
                continue
            if not license_path.is_file():
                errors.append(f"MISSING_LICENSE:{asset.asset_id}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != asset.sha256:
                errors.append(f"HASH_MISMATCH:{asset.asset_id}")
            hashes.setdefault(digest, []).append(asset.asset_id)
            if asset.mime_type == "image/svg+xml":
                errors.extend(self._validate_svg(asset, path))
        for digest, asset_ids in hashes.items():
            if len(asset_ids) > 1:
                errors.append(f"DUPLICATE_HASH:{digest}:{','.join(sorted(asset_ids))}")
        return errors

    def runtime_assets(self, asset_type: AssetType | None = None) -> tuple[AssetRecord, ...]:
        return tuple(
            asset
            for asset in self.load().assets
            if asset.runtime_eligible and (asset_type is None or asset.asset_type == asset_type)
        )

    def _resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"asset path escapes catalog: {relative_path}")
        return path

    @staticmethod
    def _validate_svg(asset: AssetRecord, path: Path) -> list[str]:
        errors: list[str] = []
        try:
            root = SafeElementTree.fromstring(path.read_bytes())
        except Exception:
            return [f"INVALID_SVG:{asset.asset_id}"]
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            if tag in {"script", "foreignobject"}:
                errors.append(f"UNSAFE_SVG_ELEMENT:{asset.asset_id}:{tag}")
            for name, value in element.attrib.items():
                attribute = name.rsplit("}", 1)[-1].lower()
                normalized = value.strip().lower()
                external_prefixes = ("http:", "https:", "javascript:")
                if attribute.startswith("on") or normalized.startswith(external_prefixes):
                    errors.append(f"UNSAFE_SVG_ATTRIBUTE:{asset.asset_id}:{attribute}")
        return errors


def load_manifest_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))
