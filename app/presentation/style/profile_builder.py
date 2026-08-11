import uuid

from app.presentation.style.archetype_clusterer import cluster_layout_archetypes
from app.presentation.style.features import StyleFeatureSet
from app.presentation.style.token_normalizer import build_design_tokens
from app.schemas.style_profile import ComponentSkin, ConfidenceReport, VisualStyleProfileV2


class StyleProfileBuilder:
    compiler_version = "style-compiler-v1"

    def build(
        self,
        features: StyleFeatureSet,
        *,
        profile_id: uuid.UUID | None = None,
    ) -> VisualStyleProfileV2:
        tokens = build_design_tokens(features)
        archetypes = cluster_layout_archetypes(features)
        warnings = []
        if not features.page_samples:
            warnings.append("NO_SANITIZED_PAGE_SAMPLES")
        if not features.page_samples and features.master_layouts:
            warnings.append("MASTER_LAYOUT_NAME_IGNORED_GEOMETRY_USED")
        if len(features.page_samples) < 3:
            warnings.append("LOW_PAGE_SAMPLE_COUNT")
        layout_confidence = round(
            sum(item.confidence * item.frequency for item in archetypes)
            / sum(item.frequency for item in archetypes),
            3,
        )
        color_confidence = min(0.95, round(0.35 + len(features.color_samples) * 0.05, 3))
        typography_confidence = min(
            0.95, round(0.35 + len(features.font_samples) * 0.08, 3)
        )
        return VisualStyleProfileV2(
            profile_id=profile_id or uuid.uuid4(),
            design_tokens=tokens,
            component_skins=self._skins(tokens.shape.radius, tokens.shape.border_width),
            layout_archetypes=archetypes,
            confidence_report=ConfidenceReport(
                overall=round(
                    (color_confidence + typography_confidence + layout_confidence) / 3,
                    3,
                ),
                color=color_confidence,
                typography=typography_confidence,
                layout=layout_confidence,
                warnings=tuple(warnings),
            ),
            compiler_version=self.compiler_version,
        )

    @staticmethod
    def _skins(radius: int, border_width: int) -> tuple[ComponentSkin, ...]:
        common = {
            "border_token": "border",
            "border_width": border_width,
            "radius": radius,
            "padding": 300,
            "heading_style": "subtitle",
            "body_style": "body",
            "alignment": "left",
            "vertical_alignment": "top",
            "accent_placement": "none",
        }
        return (
            ComponentSkin(
                token="plain_text",
                applies_to=("title", "key_message"),
                fill_token="canvas",
                **common,
            ),
            ComponentSkin(
                token="outline_card",
                applies_to=("key_message", "evidence_card"),
                fill_token="canvas",
                **{**common, "border_width": max(1, border_width)},
            ),
            ComponentSkin(
                token="filled_card",
                applies_to=(
                    "key_message",
                    "evidence_card",
                    "metric",
                    "comparison",
                    "timeline",
                    "process",
                    "source_list",
                ),
                fill_token="surface",
                **{**common, "accent_placement": "top"},
            ),
        )
