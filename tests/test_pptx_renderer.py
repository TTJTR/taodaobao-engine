import asyncio
import json
import shutil
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import ExportStatus
from app.presentation.layouts.engine import LayoutEngine
from app.schemas.presentation import PresentationSpecData, VisualStyleProfileData
from app.services import presentation_worker
from app.services.html_renderer import HTMLRenderer
from app.services.pptx_renderer import PPTXRenderer, PPTXRenderError

RENDERER_DIR = Path(__file__).parents[1] / "sidecar" / "pptx-renderer"
NODE_READY = shutil.which("node") is not None and (RENDERER_DIR / "node_modules").is_dir()


def _style() -> VisualStyleProfileData:
    return VisualStyleProfileData.model_validate(
        {
            "palette": {
                "primary": "#123456",
                "secondary": "#345678",
                "accent": "#E11D48",
                "background": "#FFFFFF",
                "foreground": "#111827",
            },
            "typography": {
                "heading_font": "Microsoft YaHei",
                "body_font": "Arial",
                "base_size_px": 18,
                "scale_ratio": 1.5,
            },
            "spacing_grid": {
                "base_unit_px": 8,
                "slide_padding_units": 8,
                "component_gap_units": 3,
            },
            "layout_grammar": ["title-and-evidence"],
        }
    )


def _positioned_spec(fact_text: str):
    claim_id = uuid.uuid4()
    semantic = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "two_column",
                    "components": [
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "title",
                            "text": "可信方案",
                        },
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "key_message",
                            "text": fact_text,
                            "fact_binding": {
                                "claim_id": claim_id,
                                "claim_key": "verified:revenue",
                                "evidence_ids": [uuid.uuid4()],
                                "source_ids": [uuid.uuid4()],
                                "content_mode": "verbatim",
                            },
                        },
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "evidence_card",
                            "heading": "证据原文",
                            "body": fact_text,
                            "fact_binding": {
                                "claim_id": claim_id,
                                "claim_key": "verified:revenue",
                                "evidence_ids": [uuid.uuid4()],
                                "source_ids": [uuid.uuid4()],
                                "content_mode": "verbatim",
                            },
                        },
                    ],
                }
            ],
        }
    )
    return LayoutEngine().position(semantic)


@pytest.mark.asyncio
@pytest.mark.skipif(not NODE_READY, reason="Node renderer dependencies are not installed")
async def test_pptx_renderer_preserves_validated_fact_and_slide_count(tmp_path: Path) -> None:
    fact_text = "2025年营业收入为1438亿元"
    spec = _positioned_spec(fact_text)
    result = await PPTXRenderer(output_directory=tmp_path).render(
        spec, _style(), artifact_id=str(uuid.uuid4())
    )

    assert result.slide_count == len(spec.slides)
    assert result.path.read_bytes().startswith(b"PK")
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()
        slide_names = [
            name
            for name in names
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        ]
        slide_xml = "".join(archive.read(name).decode("utf-8") for name in slide_names)
    assert len(slide_names) == len(spec.slides)
    assert fact_text in slide_xml
    assert fact_text in HTMLRenderer().render(spec, _style())


@pytest.mark.asyncio
@pytest.mark.skipif(not NODE_READY, reason="Node renderer dependencies are not installed")
async def test_node_renderer_fails_closed_for_unknown_component(tmp_path: Path) -> None:
    spec = _positioned_spec("不可改写的事实").model_dump(mode="json")
    spec["slides"][0]["components"][0]["component"]["component_type"] = "arbitrary_code"
    process = await asyncio.create_subprocess_exec(
        "node",
        str(RENDERER_DIR / "render.mjs"),
        str(tmp_path / "invalid.pptx"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate(
        json.dumps({"spec": spec, "style": _style().model_dump(mode="json")}).encode()
    )

    assert process.returncode != 0
    assert b"unknown component type" in stderr
    assert not (tmp_path / "invalid.pptx").exists()


@pytest.mark.asyncio
async def test_pptx_renderer_rejects_missing_fixed_script(tmp_path: Path) -> None:
    with pytest.raises(PPTXRenderError, match="script is unavailable"):
        await PPTXRenderer(
            script_path=tmp_path / "missing.mjs", output_directory=tmp_path
        ).render(_positioned_spec("事实"), _style(), artifact_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_export_worker_uses_positioned_spec_for_pptx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _positioned_spec("已验证事实")
    export = SimpleNamespace(
        id=uuid.uuid4(),
        html_artifact_id=uuid.uuid4(),
        presentation_id=spec.presentation_id,
        export_type="pptx",
        status=ExportStatus.QUEUED,
        object_key=None,
        provider_mode="pending",
    )
    artifact = SimpleNamespace(id=export.html_artifact_id)
    run = SimpleNamespace(
        id=spec.presentation_id,
        style_profile_id=uuid.uuid4(),
        spec=spec.model_dump(mode="json"),
    )
    profile = SimpleNamespace(visual_json=_style().model_dump(mode="json"))
    entities = iter([export, artifact, run, profile])
    monkeypatch.setattr(
        presentation_worker, "_entity", AsyncMock(side_effect=lambda *_: next(entities))
    )
    rendered_path = tmp_path / "result.pptx"
    rendered_path.write_bytes(b"PK-test")
    renderer = AsyncMock()
    renderer.render.return_value = SimpleNamespace(path=rendered_path)
    monkeypatch.setattr(presentation_worker, "PPTXRenderer", lambda: renderer)
    task = SimpleNamespace(stage="queued", heartbeat_at=None, lease_expires_at=None)
    session = SimpleNamespace(commit=AsyncMock())
    provider = SimpleNamespace(mode="mock", export=AsyncMock())

    await presentation_worker._export_presentation(
        session, uuid.uuid4(), export.id, task, provider
    )

    assert export.status == ExportStatus.READY
    assert export.object_key == str(rendered_path)
    assert export.provider_mode == "deterministic-pptx-v1"
    assert task.stage == "exporting"
    provider.export.assert_not_awaited()
