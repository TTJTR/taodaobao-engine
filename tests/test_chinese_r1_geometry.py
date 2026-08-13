from app.presentation.capacity.catalog import chinese_capacity_catalog
from app.presentation.capacity.geometry import VARIANT_GEOMETRY, validate_geometry


def test_all_capacity_variants_have_frozen_geometry() -> None:
    validate_geometry()
    assert set(VARIANT_GEOMETRY) == {
        variant.variant_id for variant in chinese_capacity_catalog.variants
    }


def test_geometry_uses_fixed_1600_by_900_canvas() -> None:
    for slots in VARIANT_GEOMETRY.values():
        for box in slots.values():
            assert box.x + box.width <= 1600
            assert box.y + box.height <= 900
