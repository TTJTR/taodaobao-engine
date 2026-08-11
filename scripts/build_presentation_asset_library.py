# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = REPO_ROOT / ".local" / "oss-review" / "presenton-src"
CATALOG = REPO_ROOT / "app" / "assets" / "presentation"
REVISION = "b940f7b4a51d473af830d96d441276af3540dae6"
GOOGLE_FONTS_REVISION = "038b637da7b3fd956a4ed93ffc607c3d5e4ce172"
NOTO_SANS_SC_URL = (
    "https://raw.githubusercontent.com/google/fonts/"
    f"{GOOGLE_FONTS_REVISION}/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
)
TEMPLATES = ("executive", "modern", "momentum")
FONT_LICENSE = "licenses/FONT-OFL-1.1.txt"
PRESENTON_LICENSE = "licenses/PRESENTON-APACHE-2.0.txt"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def copy(source: Path, relative_target: str) -> Path:
    target = CATALOG / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def source_url(relative: str) -> str:
    encoded = "/".join(quote(part) for part in Path(relative).parts)
    return f"https://github.com/presenton/presenton/blob/{REVISION}/{encoded}"


def record(
    *,
    asset_id: str,
    asset_type: str,
    local_path: str,
    upstream_path: str,
    license_name: str,
    license_path: str,
    mime_type: str,
    purpose: str,
    runtime_eligible: bool,
    dimensions: tuple[int, int] | None = None,
    source_override: str | None = None,
    project: str = "presenton/presenton",
    revision: str = REVISION,
) -> dict[str, object]:
    path = CATALOG / local_path
    value: dict[str, object] = {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "local_path": local_path,
        "source_url": source_override or source_url(upstream_path),
        "upstream_project": project,
        "upstream_revision": revision,
        "license": license_name,
        "license_path": license_path,
        "sha256": digest(path),
        "mime_type": mime_type,
        "purpose": purpose,
        "commercial_status": "allowed",
        "brand_confirmation_status": "not_applicable",
        "runtime_eligible": runtime_eligible,
    }
    if dimensions:
        value["width"], value["height"] = dimensions
    return value


def build() -> list[dict[str, object]]:
    assets: list[dict[str, object]] = []
    known_hashes: set[str] = set()
    for template in TEMPLATES:
        upstream_root = UPSTREAM / "templates" / template
        template_target = copy(
            upstream_root / "template.json", f"templates/presenton/{template}/template.json"
        )
        assets.append(
            record(
                asset_id=f"template.presenton.{template}",
                asset_type="template",
                local_path=f"templates/presenton/{template}/template.json",
                upstream_path=f"templates/{template}/template.json",
                license_name="Apache-2.0",
                license_path=PRESENTON_LICENSE,
                mime_type="application/json",
                purpose=f"Presenton Template v2 candidate: {template}",
                runtime_eligible=True,
            )
        )
        known_hashes.add(digest(template_target))

        thumbnail_source = upstream_root / "static" / "thumbnail.png"
        thumbnail_relative = f"thumbnails/{template}.png"
        thumbnail_target = copy(thumbnail_source, thumbnail_relative)
        assets.append(
            record(
                asset_id=f"thumbnail.presenton.{template}",
                asset_type="thumbnail",
                local_path=thumbnail_relative,
                upstream_path=f"templates/{template}/static/thumbnail.png",
                license_name="Apache-2.0",
                license_path=PRESENTON_LICENSE,
                mime_type="image/png",
                purpose=f"Human review thumbnail for {template}",
                runtime_eligible=False,
                dimensions=png_size(thumbnail_target),
            )
        )
        known_hashes.add(digest(thumbnail_target))

        for source in sorted((upstream_root / "static").glob("*.svg")):
            source_hash = digest(source)
            if source_hash in known_hashes:
                continue
            relative = f"decorations/presenton/{template}/{source.name}"
            target = copy(source, relative)
            assets.append(
                record(
                    asset_id=f"decoration.presenton.{template}.{source.stem.lower()}",
                    asset_type="decoration",
                    local_path=relative,
                    upstream_path=f"templates/{template}/static/{source.name}",
                    license_name="Apache-2.0",
                    license_path=PRESENTON_LICENSE,
                    mime_type="image/svg+xml",
                    purpose=f"Non-semantic decorative vector for {template}",
                    runtime_eligible=True,
                )
            )
            known_hashes.add(digest(target))

    for template in ("executive", "momentum"):
        static_root = UPSTREAM / "templates" / template / "static"
        for source in sorted(static_root.glob("*.ttf")):
            source_hash = digest(source)
            if source_hash in known_hashes:
                continue
            normalized = source.name.lower().replace(" ", "-")
            relative = f"fonts/{normalized}"
            target = copy(source, relative)
            assets.append(
                record(
                    asset_id=f"font.{source.stem.lower().replace(' ', '-')}",
                    asset_type="font",
                    local_path=relative,
                    upstream_path=f"templates/{template}/static/{source.name}",
                    license_name="OFL-1.1",
                    license_path=FONT_LICENSE,
                    mime_type="font/ttf",
                    purpose="Bundled presentation typography",
                    runtime_eligible=True,
                )
            )
            known_hashes.add(digest(target))
    noto_source = REPO_ROOT / ".local" / "font-sources" / "NotoSansSC-VariableFont_wght.ttf"
    if not noto_source.is_file():
        noto_source.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(NOTO_SANS_SC_URL, noto_source)
    noto_relative = "fonts/noto-sans-sc-variable-wght.ttf"
    noto_target = copy(noto_source, noto_relative)
    assets.append(
        record(
            asset_id="font.noto-sans-sc-variable-wght",
            asset_type="font",
            local_path=noto_relative,
            upstream_path="ofl/notosanssc/NotoSansSC[wght].ttf",
            license_name="OFL-1.1",
            license_path=FONT_LICENSE,
            mime_type="font/ttf",
            purpose="R1 trusted Simplified Chinese presentation typography",
            runtime_eligible=True,
            source_override=NOTO_SANS_SC_URL,
            project="google/fonts",
            revision=GOOGLE_FONTS_REVISION,
        )
    )
    known_hashes.add(digest(noto_target))
    return assets


def build_gallery(assets: list[dict[str, object]]) -> None:
    cards = []
    for template in TEMPLATES:
        template_record = next(item for item in assets if item["asset_id"] == f"template.presenton.{template}")
        cards.append(
            f'''<article class="card">
  <img src="../thumbnails/{template}.png" alt="{template} template thumbnail">
  <div class="meta"><span>Presenton Template v2</span><h2>{template.title()}</h2>
  <p>{template_record["purpose"]}</p><code>{str(template_record["sha256"])[:12]}</code></div>
</article>'''
        )
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>演示素材库评审</title><style>
:root{{--ink:#111827;--paper:#f3f4f6;--signal:#ef3e42;--muted:#6b7280}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,sans-serif}}
header{{min-height:34vh;padding:clamp(32px,6vw,88px);display:grid;align-content:end;background:#101318;color:white;border-bottom:8px solid var(--signal)}}
h1{{font-size:clamp(36px,6vw,82px);margin:0;letter-spacing:0}}header p{{max-width:720px;font-size:clamp(16px,2vw,24px);color:#cbd5e1}}
main{{padding:clamp(24px,5vw,72px);display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:24px}}
.card{{background:white;border:1px solid #d1d5db;border-radius:6px;overflow:hidden;box-shadow:0 12px 30px #11182712}}
.card img{{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#e5e7eb}}
.meta{{padding:20px}}.meta span{{color:var(--signal);font-weight:700;text-transform:uppercase}}h2{{font-size:30px;margin:6px 0}}p{{line-height:1.6}}code{{color:var(--muted)}}
footer{{padding:24px clamp(24px,5vw,72px) 56px;color:var(--muted)}}
</style></head><body><header><div><p>AI for Process / Curated Runtime Inputs</p><h1>演示素材库</h1><p>成熟模板优先，来源与许可可追溯。业务照片排除，品牌素材待官方确认。</p></div></header>
<main>{''.join(cards)}</main><footer>Catalog schema: presentation-assets-v1 · Pinned upstream: {REVISION[:12]}</footer></body></html>'''
    gallery = CATALOG / "gallery" / "index.html"
    gallery.parent.mkdir(parents=True, exist_ok=True)
    gallery.write_text(html, encoding="utf-8")


def main() -> int:
    if not UPSTREAM.is_dir():
        raise SystemExit(f"missing pinned Presenton checkout: {UPSTREAM}")
    assets = build()
    manifest = {
        "schema_version": "presentation-assets-v1",
        "generated_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "assets": assets,
    }
    (CATALOG / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    build_gallery(assets)
    print(f"built {len(assets)} presentation assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
