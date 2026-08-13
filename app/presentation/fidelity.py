import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.presentation import PositionedPresentationSpec


class FidelityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CrossFormatFidelityReport(FidelityModel):
    passed: bool
    fact_text_exact: bool
    slide_count_match: bool
    geometry_assessment: Literal["visual_approximation", "outside_tolerance"]
    maximum_geometry_delta_ratio: float = Field(ge=0)
    expected_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    errors: tuple[str, ...] = ()
    disclaimer: Literal[
        "HTML与PPTX采用不同排版引擎，仅保证事实完全一致和视觉近似，不承诺像素级一致。"
    ] = "HTML与PPTX采用不同排版引擎，仅保证事实完全一致和视觉近似，不承诺像素级一致。"


def expected_texts(spec: PositionedPresentationSpec) -> tuple[str, ...]:
    values: list[str] = []
    for slide in spec.slides:
        for positioned in slide.components:
            values.extend(_display_text(positioned.component.model_dump(mode="python")))
    return tuple(values)


def content_sha256(texts: tuple[str, ...]) -> str:
    payload = json.dumps(texts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assess_cross_format_fidelity(
    spec: PositionedPresentationSpec,
    *,
    html_texts: tuple[str, ...],
    pptx_texts: tuple[str, ...],
    html_slide_count: int,
    pptx_slide_count: int,
    maximum_geometry_delta_ratio: float,
    geometry_tolerance: float = 0.08,
) -> CrossFormatFidelityReport:
    expected = expected_texts(spec)
    errors: list[str] = []
    fact_text_exact = html_texts == expected and pptx_texts == expected
    if not fact_text_exact:
        errors.append("CROSS_FORMAT_FACT_TEXT_MISMATCH")
    slide_count_match = html_slide_count == pptx_slide_count == len(spec.slides)
    if not slide_count_match:
        errors.append("CROSS_FORMAT_SLIDE_COUNT_MISMATCH")
    geometry = (
        "visual_approximation"
        if maximum_geometry_delta_ratio <= geometry_tolerance
        else "outside_tolerance"
    )
    if geometry == "outside_tolerance":
        errors.append("CROSS_FORMAT_GEOMETRY_OUTSIDE_TOLERANCE")
    return CrossFormatFidelityReport(
        passed=not errors,
        fact_text_exact=fact_text_exact,
        slide_count_match=slide_count_match,
        geometry_assessment=geometry,
        maximum_geometry_delta_ratio=maximum_geometry_delta_ratio,
        expected_content_sha256=content_sha256(expected),
        errors=tuple(errors),
    )


def _display_text(value: object) -> list[str]:
    fields = {"text", "heading", "body", "label", "value"}
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            if key in fields and isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict | list | tuple):
                result.extend(_display_text(item))
        return result
    if isinstance(value, list | tuple):
        return [text for item in value for text in _display_text(item)]
    return []
