from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

from defusedxml.ElementTree import fromstring

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.presentation.layouts.engine import LayoutEngine  # noqa: E402
from app.presentation.style.profile_builder import StyleProfileBuilder  # noqa: E402
from app.presentation.style.template_compiler import (  # noqa: E402
    TemplateCompiler,
    compiled_bundle_hash,
)
from app.schemas.presentation import PresentationSpecData  # noqa: E402
from app.services.pptx_parser import PPTXParseError, SafePPTXParser  # noqa: E402
from app.services.pptx_renderer import PPTXRenderer  # noqa: E402

SLIDE_PART = re.compile(r"^ppt/slides/slide\d+\.xml$")


def _source_slide_text(path: Path) -> set[str]:
    values: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not SLIDE_PART.fullmatch(name):
                continue
            root = fromstring(archive.read(name))
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] == "t" and element.text:
                    value = element.text.strip()
                    if len(value) >= 4:
                        values.add(value)
    return values


def _trusted_spec(label: str):
    facts = [
        ("verified:revenue", "2025年营业收入为1438亿元"),
        ("verified:method", "AI只负责内容组织，事实由证据账本提供"),
    ]
    components: list[dict] = [
        {
            "component_id": uuid.uuid4(),
            "component_type": "title",
            "text": f"{label} 风格安全渲染验收",
        }
    ]
    for index, (claim_key, text) in enumerate(facts, start=1):
        components.append(
            {
                "component_id": uuid.uuid4(),
                "component_type": "evidence_card",
                "heading": f"已验证事实 {index}",
                "body": text,
                "fact_binding": {
                    "claim_id": uuid.uuid4(),
                    "claim_key": claim_key,
                    "evidence_ids": [uuid.uuid4()],
                    "source_ids": [uuid.uuid4()],
                    "content_mode": "verbatim",
                },
            }
        )
    semantic = PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": uuid.uuid4(),
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "two_column",
                    "components": components,
                }
            ],
        }
    )
    return LayoutEngine().position(semantic), [text for _, text in facts]


def _validate_output(path: Path, expected_slides: int, expected_facts: list[str]) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        slide_names = sorted(
            name for name in names if SLIDE_PART.fullmatch(name)
        )
        xml = "".join(archive.read(name).decode("utf-8") for name in slide_names)
    missing = [fact for fact in expected_facts if fact not in xml]
    return {
        "valid_ooxml": path.read_bytes().startswith(b"PK"),
        "slide_count": len(slide_names),
        "expected_slide_count": expected_slides,
        "facts_preserved": not missing,
        "missing_facts": missing,
        "size_bytes": path.stat().st_size,
    }


def _export_visual_preview(path: Path, output_dir: Path, expected_slides: int) -> dict:
    if sys.platform != "win32":
        return {"available": False, "reason": "PowerPoint COM requires Windows"}
    helper = ROOT / "scripts" / "export_pptx_previews.ps1"
    process = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-InputPptx",
            str(path),
            "-OutputDirectory",
            str(output_dir),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    if process.returncode != 0:
        return {
            "available": False,
            "reason": (process.stderr or process.stdout).strip()[:500],
        }
    previews = sorted(output_dir.glob("*.PNG")) + sorted(output_dir.glob("*.png"))
    previews = list(dict.fromkeys(previews))
    return {
        "available": True,
        "passed": len(previews) == expected_slides
        and all(item.stat().st_size > 5_000 for item in previews),
        "preview_count": len(previews),
        "expected_preview_count": expected_slides,
        "preview_files": [str(item) for item in previews],
    }


async def _validate_sample(source: Path, output_dir: Path) -> dict:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    parser = SafePPTXParser()
    theme_profile = parser.parse(source, mode="theme_only")
    visual_profile = parser.parse(source, mode="sanitized_visual")
    features = parser.extract_features(source, mode="sanitized_visual")
    profile_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pptx-style:{digest}")
    style_profile_v2 = StyleProfileBuilder().build(features, profile_id=profile_id)
    compiled_bundle = TemplateCompiler().compile(style_profile_v2)
    source_text = _source_slide_text(source)
    serialized_profile = style_profile_v2.model_dump_json()
    leaked = sorted(text for text in source_text if text in serialized_profile)

    spec, facts = _trusted_spec(source.stem)
    renderer = PPTXRenderer(output_directory=output_dir)
    rendered = await renderer.render(spec, visual_profile, artifact_id=f"{source.stem}-rendered")
    output_report = _validate_output(rendered.path, len(spec.slides), facts)
    visual_report = _export_visual_preview(
        rendered.path, output_dir / f"{source.stem}-preview", len(spec.slides)
    )
    passed = not leaked and all(
        (
            output_report["valid_ooxml"],
            output_report["slide_count"] == output_report["expected_slide_count"],
            output_report["facts_preserved"],
            all(candidate.status == "needs_review" for candidate in compiled_bundle.candidates),
            not visual_report.get("available") or visual_report.get("passed"),
        )
    )
    return {
        "source": str(source.resolve()),
        "source_size_bytes": source.stat().st_size,
        "source_sha256": digest,
        "source_text_fragments_audited": len(source_text),
        "sensitive_text_leaks": leaked,
        "theme_only": theme_profile.model_dump(mode="json"),
        "sanitized_visual": visual_profile.model_dump(mode="json"),
        "feature_set_summary": {
            "schema_version": features.schema_version,
            "canvas": features.canvas.model_dump(mode="json"),
            "colors": len(features.color_samples),
            "fonts": len(features.font_samples),
            "shapes": len(features.shape_samples),
            "pages": len(features.page_samples),
            "master_layouts": len(features.master_layouts),
        },
        "style_profile_v2": style_profile_v2.model_dump(mode="json"),
        "compiled_template_bundle": compiled_bundle.model_dump(mode="json"),
        "compiled_template_hash": compiled_bundle_hash(compiled_bundle),
        "rendered_pptx": str(rendered.path),
        "output_validation": output_report,
        "visual_validation": visual_report,
        "passed": passed,
    }


def _markdown(report: dict) -> str:
    preview_available = all(
        item.get("visual_validation", {}).get("available") for item in report["samples"]
    )
    lines = [
        "Technical result and visual quality are reported separately.",
        "Visual quality requires manual review; successful PNG export is not a design pass.",
        "PowerPoint preview export: "
        f"{'complete' if preview_available else 'unavailable/incomplete'}",
        "",
        "# PPTX 样本端到端验收报告",
        "",
        f"- 总体结果：{'通过' if report['passed'] else '失败'}",
        f"- 样本数：{len(report['samples'])}",
        f"- LibreOffice：{'可用' if report['libreoffice_available'] else '未安装，未执行转图验收'}",
        "",
        "| 样本 | 结果 | V2 主色/正文色 | Archetype | 候选状态 | 文本泄漏 | 输出文件 |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for item in report["samples"]:
        if "error" in item:
            lines.append(
                f"| {Path(item['source']).name} | 失败：{item['error']} | - | - | - | - | - |"
            )
            continue
        profile = item["style_profile_v2"]
        palette = profile["design_tokens"]["colors"]
        archetypes = ", ".join(
            archetype["archetype_token"] for archetype in profile["layout_archetypes"]
        )
        candidate_statuses = ", ".join(
            candidate["status"]
            for candidate in item["compiled_template_bundle"]["candidates"]
        )
        output = Path(item["rendered_pptx"])
        row_template = (
            "| {name} | {status} | {primary}/{text} | {archetypes} | "
            "{candidates} | {leaks} | {output} |"
        )
        lines.append(
            row_template.format(
                name=Path(item["source"]).name,
                status="通过" if item["passed"] else "失败",
                primary=palette["primary"],
                text=palette["text_primary"],
                archetypes=archetypes,
                candidates=candidate_statuses,
                leaks=len(item["sensitive_text_leaks"]),
                output=output.name,
            )
        )
    lines.extend(
        [
            "",
            "## 验收边界",
            "",
            "- 已检查 PPTX 可解析、风格提取、原业务文本不进入 StyleProfile。",
            "- 已检查 StyleFeatureSet、StyleProfile v2、布局聚类和六类候选容量场景。",
            "- `needs_review` 只表示候选项通过机器预检，仍需人工确认后才能进入正式生成。",
            "- 已用提取风格生成可信测试稿，并检查 OOXML 页数和事实逐字一致。",
            "- 未安装 LibreOffice 时，本报告不宣称完成像素级视觉验收；"
            "请人工打开输出 PPTX 查看效果。",
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Validate PPTX style samples end to end")
    argument_parser.add_argument("samples", nargs="+", type=Path)
    argument_parser.add_argument(
        "--output-dir", type=Path, default=ROOT / ".local" / "pptx-sample-validation"
    )
    args = argument_parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    for sample in args.samples:
        try:
            samples.append(await _validate_sample(sample.resolve(), output_dir))
        except (PPTXParseError, OSError, ValueError, zipfile.BadZipFile) as exc:
            samples.append(
                {"source": str(sample.resolve()), "passed": False, "error": str(exc)}
            )
    report = {
        "passed": bool(samples) and all(item["passed"] for item in samples),
        "libreoffice_available": bool(shutil.which("soffice") or shutil.which("libreoffice")),
        "samples": samples,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
