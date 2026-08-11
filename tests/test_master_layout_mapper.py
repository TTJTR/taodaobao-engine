from app.presentation.style.features import (
    CanvasFeature,
    MasterLayoutFeature,
    MasterPlaceholder,
    NormalizedGeometry,
    StyleFeatureSet,
)
from app.presentation.style.master_layout_mapper import map_master_layout
from app.presentation.style.profile_builder import StyleProfileBuilder


def _layout(name: str) -> MasterLayoutFeature:
    return MasterLayoutFeature(
        name=name,
        placeholders=(
            MasterPlaceholder(
                role="奇怪标题",
                geometry=NormalizedGeometry(x=600, y=500, width=8800, height=1000),
            ),
            MasterPlaceholder(
                role="左边随便叫",
                geometry=NormalizedGeometry(x=600, y=2200, width=4100, height=6200),
            ),
            MasterPlaceholder(
                role="右边随便叫",
                geometry=NormalizedGeometry(x=5300, y=2200, width=4100, height=6200),
            ),
        ),
    )


def test_rogue_master_name_never_changes_geometry_mapping() -> None:
    chinese_name = map_master_layout(_layout("张总版式"))
    random_name = map_master_layout(_layout("FINAL_v9_千万别动"))

    assert chinese_name.archetype == random_name.archetype == "title_two_column"
    assert chinese_name.reason == "placeholder_geometry_two_columns"
    assert random_name.reason == "placeholder_geometry_two_columns"
    assert chinese_name.source_name != random_name.source_name


def test_master_without_geometry_fails_closed_to_system_body_layout() -> None:
    mapping = map_master_layout(
        MasterLayoutFeature(
            name="张总版式",
            placeholders=(MasterPlaceholder(role="未知", geometry=None),),
        )
    )

    assert mapping.archetype == "title_body"
    assert mapping.reason == "system_fallback_no_geometry"
    assert mapping.confidence == 0.3


def test_style_profile_uses_geometry_and_records_master_name_is_ignored() -> None:
    features = StyleFeatureSet(
        canvas=CanvasFeature(width_emu=12_192_000, height_emu=6_858_000, aspect_ratio=16 / 9),
        color_samples=(),
        font_samples=(),
        shape_samples=(),
        page_samples=(),
        master_layouts=(_layout("张总版式"),),
        source_mode="theme_only",
    )

    profile = StyleProfileBuilder().build(features)

    assert profile.layout_archetypes[0].archetype_token == "title_two_column"
    assert "MASTER_LAYOUT_NAME_IGNORED_GEOMETRY_USED" in profile.confidence_report.warnings
