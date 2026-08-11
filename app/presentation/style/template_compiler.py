import hashlib
import json
import uuid

from app.presentation.layouts.models import Capacity, LayoutTemplate, Slot
from app.presentation.layouts.registry import default_layout_registry
from app.presentation.style.template_candidates import (
    CandidateValidationReport,
    CompiledTemplateBundle,
    TemplateCandidate,
    TemplateCandidateEvaluator,
)
from app.schemas.presentation import ComponentGeometry, LayoutToken
from app.schemas.style_profile import ArchetypeSlot, LayoutArchetype, VisualStyleProfileV2

ARCHETYPE_TO_LAYOUT: dict[str, LayoutToken] = {
    "title_body": "title_body",
    "title_two_column": "two_column",
    "title_three_cards": "three_cards",
}


class TemplateCompiler:
    """Compile anonymous visual observations into the restricted runtime layout model."""

    def __init__(self, evaluator: TemplateCandidateEvaluator | None = None) -> None:
        self.evaluator = evaluator or TemplateCandidateEvaluator()

    def compile(self, profile: VisualStyleProfileV2) -> CompiledTemplateBundle:
        candidates = []
        warnings = []
        for archetype in profile.layout_archetypes:
            token = ARCHETYPE_TO_LAYOUT.get(archetype.archetype_token)
            if token is None:
                warnings.append(f"UNSUPPORTED_ARCHETYPE:{archetype.archetype_token}")
                continue
            template = self._compile_archetype(archetype, token)
            validation = self._evaluate_for_rendering(template, profile)
            if not validation.passed:
                warnings.append(f"FALLBACK:{archetype.archetype_token}")
                template = default_layout_registry.get(token)
                validation = self._evaluate_for_rendering(template, profile)
            candidates.append(
                TemplateCandidate(
                    candidate_id=uuid.uuid5(profile.profile_id, archetype.archetype_token),
                    style_profile_id=profile.profile_id,
                    archetype_token=archetype.archetype_token,
                    status="needs_review" if validation.passed else "failed",
                    confidence=archetype.confidence,
                    layout_template=template,
                    validation=validation,
                )
            )
        if not candidates:
            fallback = default_layout_registry.get("title_body")
            validation = self.evaluator.evaluate(fallback)
            candidates.append(
                TemplateCandidate(
                    candidate_id=uuid.uuid5(profile.profile_id, "system:title_body"),
                    style_profile_id=profile.profile_id,
                    archetype_token="title_body",
                    status="needs_review" if validation.passed else "failed",
                    confidence=0,
                    layout_template=fallback,
                    validation=validation,
                )
            )
            warnings.append("NO_SUPPORTED_ARCHETYPE")
        return CompiledTemplateBundle(
            style_profile_id=profile.profile_id,
            compiler_version=profile.compiler_version,
            candidates=tuple(candidates),
            warnings=tuple(warnings),
        )

    def _evaluate_for_rendering(
        self, template: LayoutTemplate, profile: VisualStyleProfileV2
    ) -> CandidateValidationReport:
        report = self.evaluator.evaluate(template)
        render_errors = self._render_capacity_errors(template, profile)
        return report.model_copy(
            update={
                "passed": report.passed and not render_errors,
                "structural_errors": (*report.structural_errors, *render_errors),
            }
        )

    @staticmethod
    def _render_capacity_errors(
        template: LayoutTemplate, profile: VisualStyleProfileV2
    ) -> tuple[str, ...]:
        typography = profile.design_tokens.typography
        errors = []
        for slot in template.slots:
            component_types = set(slot.allowed_component_types)
            if component_types == {"title"}:
                continue
            skins = [
                skin
                for skin in profile.component_skins
                if component_types.intersection(skin.applies_to)
            ]
            padding = max((skin.padding for skin in skins), default=0)
            width_px = slot.geometry.width / 10_000 * 1600
            height_px = slot.geometry.height / 10_000 * 900
            horizontal_padding = padding / 10_000 * 1600 * 2
            vertical_padding = padding / 10_000 * 900 * 2
            inner_width = width_px - horizontal_padding
            inner_height = height_px - vertical_padding
            body_line = typography.body.size_pt * 4 / 3 * typography.body.line_spacing
            heading_line = (
                typography.subtitle.size_pt * 4 / 3 * typography.subtitle.line_spacing
            )
            required_height = body_line * 2
            if component_types.intersection(
                {"evidence_card", "metric", "comparison", "timeline", "process", "source_list"}
            ):
                required_height += heading_line * 2
            if inner_width < 160:
                errors.append(f"{slot.name}:RENDER_WIDTH_AFTER_PADDING")
            if inner_height < required_height:
                errors.append(f"{slot.name}:RENDER_HEIGHT_AFTER_PADDING")
        return tuple(errors)

    def _compile_archetype(
        self, archetype: LayoutArchetype, token: LayoutToken
    ) -> LayoutTemplate:
        slots = tuple(self._compile_slot(slot) for slot in archetype.slots)
        fallback = ARCHETYPE_TO_LAYOUT.get(archetype.fallback_archetype)
        return LayoutTemplate(token=token, slots=slots, fallback_token=fallback)

    @staticmethod
    def _compile_slot(source: ArchetypeSlot) -> Slot:
        geometry = source.geometry
        min_y = 300 if source.role == "title" else 1_800
        x = max(300, geometry.x)
        y = max(min_y, geometry.y)
        width = min(9_700 - x, geometry.width)
        height = min(9_700 - y, geometry.height)
        if width < 800 or height < 500:
            raise ValueError(f"slot {source.role} is too small after safe-area normalization")
        min_font_px = max(12, round(source.capacity.min_font_pt * 4 / 3))
        characters_per_line = max(8, int(width / 10_000 * 54 * 16 / min_font_px))
        worst_case_characters_per_line = max(8, int(characters_per_line * 0.8))
        safe_characters = min(
            source.capacity.max_characters,
            worst_case_characters_per_line * source.capacity.max_lines,
        )
        style = "heading" if source.role == "title" else "evidence"
        return Slot(
            name=source.role,
            geometry=ComponentGeometry(x=x, y=y, width=width, height=height),
            allowed_component_types=source.allowed_components,
            capacity=Capacity(
                max_components=1,
                max_characters=safe_characters,
                max_lines=source.capacity.max_lines,
                min_font_px=min_font_px,
            ),
            text_style_token=style,
        )


def compiled_bundle_hash(bundle: CompiledTemplateBundle) -> str:
    payload = json.dumps(
        bundle.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
