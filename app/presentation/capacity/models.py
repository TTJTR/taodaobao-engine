from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PageKind = Literal["cover", "section", "key_message", "evidence", "process", "closing"]
CapacityStatus = Literal["proposed", "measured"]
CapacityAction = Literal["fit", "use_compact_variant", "paginate", "reject"]


class CapacityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapacityBand(CapacityModel):
    max_cjk_units: float = Field(gt=0, le=2_000)
    max_lines: int = Field(ge=1, le=40)


class SlotCapacity(CapacityModel):
    slot_name: str = Field(min_length=1, max_length=64)
    font_role: Literal["display", "title", "statement", "body", "evidence", "metadata"]
    font_size_px: int = Field(ge=14, le=120)
    min_font_size_px: int = Field(ge=14, le=120)
    font_weight: int = Field(ge=200, le=800)
    line_height: float = Field(ge=1, le=2)
    recommended: CapacityBand
    soft_limit: CapacityBand
    hard_limit: CapacityBand
    pagination_allowed: bool = True

    @model_validator(mode="after")
    def validate_monotonic_capacity(self) -> "SlotCapacity":
        if self.min_font_size_px > self.font_size_px:
            raise ValueError("minimum font size cannot exceed the preferred font size")
        units = (
            self.recommended.max_cjk_units,
            self.soft_limit.max_cjk_units,
            self.hard_limit.max_cjk_units,
        )
        lines = (
            self.recommended.max_lines,
            self.soft_limit.max_lines,
            self.hard_limit.max_lines,
        )
        if list(units) != sorted(units) or list(lines) != sorted(lines):
            raise ValueError("capacity bands must be monotonic")
        return self


class ChineseTemplateVariant(CapacityModel):
    variant_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    page_kind: PageKind
    template_family: Literal["modern-cn-r1"] = "modern-cn-r1"
    status: CapacityStatus = "proposed"
    slots: tuple[SlotCapacity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_slots(self) -> "ChineseTemplateVariant":
        names = [slot.slot_name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError("slot names must be unique within a variant")
        return self

    def slot(self, name: str) -> SlotCapacity:
        for slot in self.slots:
            if slot.slot_name == name:
                return slot
        raise ValueError(f"unknown capacity slot: {self.variant_id}.{name}")


class ChineseCapacityCatalog(CapacityModel):
    schema_version: Literal["chinese-capacity-v1"] = "chinese-capacity-v1"
    canvas_width: Literal[1600] = 1600
    canvas_height: Literal[900] = 900
    font_asset_id: Literal["font.noto-sans-sc-variable-wght"] = (
        "font.noto-sans-sc-variable-wght"
    )
    font_sha256: Literal[
        "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
    ] = "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
    variants: tuple[ChineseTemplateVariant, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> "ChineseCapacityCatalog":
        ids = [variant.variant_id for variant in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("capacity variant ids must be unique")
        required = {"cover", "section", "key_message", "evidence", "process", "closing"}
        if {variant.page_kind for variant in self.variants} != required:
            raise ValueError("capacity catalog must cover all six R1 page kinds")
        return self

    def variant(self, variant_id: str) -> ChineseTemplateVariant:
        for variant in self.variants:
            if variant.variant_id == variant_id:
                return variant
        raise ValueError(f"unknown capacity variant: {variant_id}")


class CapacityDecision(CapacityModel):
    action: CapacityAction
    tier: Literal["recommended", "soft", "hard", "overflow"]
    cjk_units: float = Field(ge=0)
    estimated_lines: int = Field(ge=0)
    minimum_font_size_px: int = Field(ge=14)
    error_code: Literal[
        "TEMPLATE_CAPACITY_EXCEEDED",
        "UNBREAKABLE_TOKEN_OVERFLOW",
        "VERBATIM_REQUIRES_PAGINATION",
    ] | None = None
