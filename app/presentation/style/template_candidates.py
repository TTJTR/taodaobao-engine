import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.presentation.layouts.models import LayoutTemplate


class CandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapacityScenarioResult(CandidateModel):
    scenario: Literal["minimum", "typical", "maximum", "cjk", "long_number", "sources"]
    passed: bool
    errors: tuple[str, ...] = ()


class CandidateValidationReport(CandidateModel):
    passed: bool
    scenarios: tuple[CapacityScenarioResult, ...]
    structural_errors: tuple[str, ...] = ()


class TemplateCandidate(CandidateModel):
    candidate_id: uuid.UUID
    style_profile_id: uuid.UUID
    archetype_token: str = Field(min_length=1, max_length=64)
    status: Literal[
        "draft",
        "previewing",
        "needs_review",
        "confirmed",
        "rejected",
        "superseded",
        "failed",
    ]
    confidence: float = Field(ge=0, le=1)
    layout_template: LayoutTemplate
    validation: CandidateValidationReport
    preview_content_set: Literal["sanitized-capacity-v1"] = "sanitized-capacity-v1"


class CompiledTemplateBundle(CandidateModel):
    schema_version: Literal["compiled-template-bundle-v1"] = "compiled-template-bundle-v1"
    style_profile_id: uuid.UUID
    compiler_version: str = Field(min_length=1, max_length=32)
    candidates: tuple[TemplateCandidate, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()


class TemplateCandidateEvaluator:
    """Preflight candidate capacity with deterministic, sanitized content envelopes."""

    scenarios = ("minimum", "typical", "maximum", "cjk", "long_number", "sources")

    def evaluate(self, template: LayoutTemplate) -> CandidateValidationReport:
        structural_errors = self._structural_errors(template)
        scenario_results = tuple(self._evaluate_scenario(template, name) for name in self.scenarios)
        return CandidateValidationReport(
            passed=not structural_errors and all(item.passed for item in scenario_results),
            scenarios=scenario_results,
            structural_errors=tuple(structural_errors),
        )

    @staticmethod
    def _structural_errors(template: LayoutTemplate) -> list[str]:
        errors = []
        for index, slot in enumerate(template.slots):
            box = slot.geometry
            if box.x + box.width > 10_000 or box.y + box.height > 10_000:
                errors.append(f"{slot.name}:OUT_OF_BOUNDS")
            for other in template.slots[:index]:
                other_box = other.geometry
                if not (
                    box.x + box.width <= other_box.x
                    or other_box.x + other_box.width <= box.x
                    or box.y + box.height <= other_box.y
                    or other_box.y + other_box.height <= box.y
                ):
                    errors.append(f"{other.name}:{slot.name}:OVERLAP")
        return errors

    @staticmethod
    def _evaluate_scenario(template: LayoutTemplate, scenario: str) -> CapacityScenarioResult:
        factors = {
            "minimum": 0.2,
            "typical": 0.6,
            "maximum": 1.0,
            "cjk": 0.9,
            "long_number": 0.85,
            "sources": 1.0,
        }
        errors = []
        for slot in template.slots:
            if slot.name == "title":
                continue
            requested = max(1, round(slot.capacity.max_characters * factors[scenario]))
            characters_per_line = max(
                8,
                int(slot.geometry.width / 10_000 * 54 * 16 / slot.capacity.min_font_px),
            )
            if scenario == "long_number":
                characters_per_line = max(8, int(characters_per_line * 0.8))
            required_lines = (requested + characters_per_line - 1) // characters_per_line
            if required_lines > slot.capacity.max_lines:
                errors.append(f"{slot.name}:LINE_CAPACITY_EXCEEDED")
        return CapacityScenarioResult(
            scenario=scenario,
            passed=not errors,
            errors=tuple(errors),
        )

