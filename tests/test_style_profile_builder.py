import copy
import uuid

from test_pptx_parser import _pptx

from app.presentation.style.features import NormalizedGeometry
from app.presentation.style.legacy_adapter import adapt_legacy_style_profile
from app.presentation.style.profile_builder import StyleProfileBuilder
from app.presentation.style.template_compiler import TemplateCompiler, compiled_bundle_hash
from app.services.pptx_parser import SafePPTXParser


def test_profile_builder_produces_bounded_tokens_and_observed_archetype() -> None:
    features = SafePPTXParser().extract_features(_pptx(), mode="sanitized_visual")
    profile = StyleProfileBuilder().build(features, profile_id=uuid.UUID(int=1))

    assert profile.schema_version == "style-profile-v2"
    assert profile.design_tokens.colors.primary == "#123456"
    assert profile.design_tokens.colors.text_primary != profile.design_tokens.colors.canvas
    assert 32 <= profile.design_tokens.typography.display.size_pt <= 48
    assert 16 <= profile.design_tokens.typography.body.size_pt <= 24
    assert profile.layout_archetypes[0].archetype_token == "title_two_column"
    assert profile.layout_archetypes[0].confidence > 0.5
    assert {skin.token for skin in profile.component_skins} >= {
        "plain_text",
        "outline_card",
        "filled_card",
    }


def test_template_compiler_is_deterministic_and_requires_review() -> None:
    features = SafePPTXParser().extract_features(_pptx(), mode="sanitized_visual")
    profile = StyleProfileBuilder().build(features, profile_id=uuid.UUID(int=2))

    first = TemplateCompiler().compile(profile)
    second = TemplateCompiler().compile(profile)

    assert compiled_bundle_hash(first) == compiled_bundle_hash(second)
    assert first.candidates[0].status == "needs_review"
    assert first.candidates[0].layout_template.token == "two_column"
    assert first.candidates[0].validation.passed is True
    assert {item.scenario for item in first.candidates[0].validation.scenarios} == {
        "minimum",
        "typical",
        "maximum",
        "cjk",
        "long_number",
        "sources",
    }


def test_legacy_provider_style_adapts_without_changing_persisted_payload() -> None:
    legacy = {
        "palette": {"primary": "#173F8A", "accent": "#FF8A3D"},
        "typography": {"title": "sans-serif", "body": "sans-serif"},
        "layout_grammar": ["top_title", "three_cards"],
        "density": "medium",
    }
    original = copy.deepcopy(legacy)

    profile = adapt_legacy_style_profile(legacy, profile_id=uuid.UUID(int=3))

    assert profile.profile_id == uuid.UUID(int=3)
    assert profile.design_tokens.colors.primary == "#173F8A"
    assert profile.confidence_report.warnings == (
        "NO_SANITIZED_PAGE_SAMPLES",
        "LOW_PAGE_SAMPLE_COUNT",
    )
    assert legacy == original


def test_template_compiler_falls_back_when_padding_and_fonts_do_not_fit() -> None:
    features = SafePPTXParser().extract_features(_pptx(), mode="sanitized_visual")
    profile = StyleProfileBuilder().build(features, profile_id=uuid.UUID(int=4))
    archetype = profile.layout_archetypes[0]
    slots = tuple(
        slot.model_copy(
            update={
                "geometry": NormalizedGeometry(x=1200, y=3900, width=1500, height=1500)
            }
        )
        if slot.role != "title"
        else slot
        for slot in archetype.slots
    )
    profile = profile.model_copy(
        update={"layout_archetypes": (archetype.model_copy(update={"slots": slots}),)}
    )

    bundle = TemplateCompiler().compile(profile)

    assert f"FALLBACK:{archetype.archetype_token}" in bundle.warnings
    assert bundle.candidates[0].validation.passed is True
    assert all(
        slot.geometry.width > 1500
        for slot in bundle.candidates[0].layout_template.slots
        if slot.name != "title"
    )
