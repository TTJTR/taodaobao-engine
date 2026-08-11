"""Chinese presentation capacity contracts and deterministic decisions."""

from app.presentation.capacity.catalog import chinese_capacity_catalog
from app.presentation.capacity.models import (
    CapacityBand,
    CapacityDecision,
    ChineseCapacityCatalog,
    ChineseTemplateVariant,
    PageKind,
    SlotCapacity,
)
from app.presentation.capacity.service import ChineseCapacityService, count_cjk_units

__all__ = [
    "CapacityBand",
    "CapacityDecision",
    "ChineseCapacityCatalog",
    "ChineseCapacityService",
    "ChineseTemplateVariant",
    "PageKind",
    "SlotCapacity",
    "chinese_capacity_catalog",
    "count_cjk_units",
]
