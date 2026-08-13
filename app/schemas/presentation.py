import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactBinding(StrictDomainModel):
    claim_id: uuid.UUID
    claim_key: str = Field(min_length=1, max_length=160)
    evidence_ids: list[uuid.UUID] = Field(min_length=1)
    source_ids: list[uuid.UUID] = Field(min_length=1)
    content_mode: Literal["verbatim", "label_only"]


class FrozenDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LedgerEvidence(FrozenDomainModel):
    evidence_id: uuid.UUID
    source_id: uuid.UUID
    source_version: int = Field(ge=1)
    quote: str = Field(min_length=1)


class FactAtom(FrozenDomainModel):
    claim_id: uuid.UUID
    claim_key: str = Field(min_length=1, max_length=160)
    verbatim_text: str = Field(min_length=1)
    boundary: str = Field(min_length=1, max_length=64)
    evidence: tuple[LedgerEvidence, ...] = Field(min_length=1)
    allowed_labels: tuple[str, ...] = ()


class FactLedger(FrozenDomainModel):
    schema_version: Literal["fact-ledger-v1"] = "fact-ledger-v1"
    run_id: uuid.UUID
    facts: tuple[FactAtom, ...]


class GuardFailure(FrozenDomainModel):
    code: Literal[
        "UNKNOWN_CLAIM",
        "CLAIM_KEY_MISMATCH",
        "UNKNOWN_EVIDENCE",
        "SOURCE_OUT_OF_BOUNDS",
        "VERBATIM_CONTENT_MISSING",
        "VERBATIM_CONTENT_MISMATCH",
        "CONTENT_FINGERPRINT_MISMATCH",
        "BOUND_COMPONENT_MISSING",
        "BOUND_COMPONENT_ADDED",
        "LEDGER_LABEL_MISMATCH",
    ]
    component_id: uuid.UUID
    claim_id: uuid.UUID
    message: str


class ValidationReport(FrozenDomainModel):
    passed: bool
    checked_components: int = Field(ge=0)
    failures: tuple[GuardFailure, ...]


class SlideComponent(StrictDomainModel):
    component_id: uuid.UUID
    component_type: str


class TitleComponent(SlideComponent):
    component_type: Literal["title"] = "title"
    text: str = Field(min_length=1, max_length=300)


class KeyMessageComponent(SlideComponent):
    component_type: Literal["key_message"] = "key_message"
    text: str = Field(min_length=1, max_length=2_000)
    fact_binding: FactBinding


class EvidenceCardComponent(SlideComponent):
    component_type: Literal["evidence_card"] = "evidence_card"
    heading: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=4_000)
    fact_binding: FactBinding


class BoundFactItem(StrictDomainModel):
    item_id: uuid.UUID
    label: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1_000)
    fact_binding: FactBinding


class MetricComponent(SlideComponent):
    component_type: Literal["metric"] = "metric"
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=300)
    fact_binding: FactBinding


class ComparisonComponent(SlideComponent):
    component_type: Literal["comparison"] = "comparison"
    heading: str = Field(min_length=1, max_length=200)
    left: BoundFactItem
    right: BoundFactItem


class TimelineComponent(SlideComponent):
    component_type: Literal["timeline"] = "timeline"
    heading: str = Field(min_length=1, max_length=200)
    items: list[BoundFactItem] = Field(min_length=2, max_length=12)


class ProcessComponent(SlideComponent):
    component_type: Literal["process"] = "process"
    heading: str = Field(min_length=1, max_length=200)
    steps: list[BoundFactItem] = Field(min_length=2, max_length=12)


class BoundSourceItem(StrictDomainModel):
    item_id: uuid.UUID
    label: str = Field(min_length=1, max_length=160)
    source_id: uuid.UUID
    fact_binding: FactBinding

    @model_validator(mode="after")
    def validate_source_binding(self) -> "BoundSourceItem":
        if self.fact_binding.content_mode != "label_only":
            raise ValueError("source list items must use label_only content mode")
        if self.source_id not in self.fact_binding.source_ids:
            raise ValueError("source_id must be included in fact_binding.source_ids")
        return self


class SourceListComponent(SlideComponent):
    component_type: Literal["source_list"] = "source_list"
    heading: str = Field(min_length=1, max_length=200)
    sources: list[BoundSourceItem] = Field(min_length=1, max_length=12)


SemanticComponent = Annotated[
    TitleComponent
    | KeyMessageComponent
    | EvidenceCardComponent
    | MetricComponent
    | ComparisonComponent
    | TimelineComponent
    | ProcessComponent
    | SourceListComponent,
    Field(discriminator="component_type"),
]

LayoutToken = Literal[
    "cover",
    "title_body",
    "two_column",
    "three_cards",
    "evidence_grid",
    "metric_highlight",
    "comparison",
    "timeline",
    "process",
    "source_list",
]


class SlideSchema(StrictDomainModel):
    slide_id: uuid.UUID
    layout_token: LayoutToken
    components: list[SemanticComponent] = Field(min_length=1)


class PresentationSpecData(StrictDomainModel):
    schema_version: str = Field(min_length=1, max_length=32)
    presentation_id: uuid.UUID
    slides: list[SlideSchema] = Field(min_length=1)


SlidePurpose = Literal[
    "cover",
    "executive_summary",
    "key_metric",
    "comparison",
    "implementation_timeline",
    "delivery_process",
    "evidence",
    "sources",
]

PlannedComponentType = Literal[
    "key_message",
    "evidence_card",
    "metric",
    "comparison",
    "timeline",
    "process",
    "source_list",
]


class SlidePlanComponent(StrictDomainModel):
    component_id: uuid.UUID
    component_type: PlannedComponentType
    claim_ids: list[uuid.UUID] = Field(min_length=1, max_length=12)
    priority: int = Field(ge=1, le=5)

    @model_validator(mode="after")
    def validate_claim_count(self) -> "SlidePlanComponent":
        count = len(self.claim_ids)
        limits = {
            "key_message": (1, 1),
            "evidence_card": (1, 1),
            "metric": (1, 1),
            "comparison": (2, 2),
            "timeline": (2, 12),
            "process": (2, 12),
            "source_list": (1, 12),
        }
        minimum, maximum = limits[self.component_type]
        if not minimum <= count <= maximum:
            raise ValueError(
                f"{self.component_type} requires between {minimum} and {maximum} claim_ids"
            )
        if len(set(self.claim_ids)) != count:
            raise ValueError("claim_ids must be unique within a planned component")
        return self


class SlidePlanPage(StrictDomainModel):
    slide_id: uuid.UUID
    purpose: SlidePurpose
    layout_token: LayoutToken
    components: list[SlidePlanComponent] = Field(default_factory=list, max_length=6)
    character_budget: int = Field(ge=80, le=2_000)
    allow_pagination: bool = True

    @model_validator(mode="after")
    def validate_layout_component_contract(self) -> "SlidePlanPage":
        types = [component.component_type for component in self.components]
        allowed = {
            "cover": (set(), 0),
            "title_body": ({"key_message", "evidence_card"}, 1),
            "two_column": ({"key_message", "evidence_card"}, 2),
            "three_cards": ({"key_message", "evidence_card"}, 3),
            "evidence_grid": ({"evidence_card"}, 4),
            "metric_highlight": ({"metric"}, 1),
            "comparison": ({"comparison"}, 1),
            "timeline": ({"timeline"}, 1),
            "process": ({"process"}, 1),
            "source_list": ({"source_list"}, 1),
        }
        allowed_types, maximum = allowed[self.layout_token]
        if len(types) > maximum or any(item not in allowed_types for item in types):
            raise ValueError("planned components are incompatible with layout_token")
        if self.layout_token != "cover" and not types:
            raise ValueError("non-cover slide plans require at least one component")
        if self.layout_token == "cover" and self.purpose != "cover":
            raise ValueError("cover layout requires cover purpose")
        return self


class SlidePlanData(StrictDomainModel):
    schema_version: Literal["slide-plan-v1"] = "slide-plan-v1"
    presentation_id: uuid.UUID
    pages: list[SlidePlanPage] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "SlidePlanData":
        slide_ids = [page.slide_id for page in self.pages]
        component_ids = [
            component.component_id for page in self.pages for component in page.components
        ]
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("slide plan slide_ids must be unique")
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("slide plan component_ids must be unique")
        return self


class SlidePlanningContext(FrozenDomainModel):
    presentation_id: uuid.UUID
    audience: str = Field(min_length=1, max_length=64)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    mode: Literal["strict", "balanced", "brand_only"] = "balanced"


class PlanningFact(FrozenDomainModel):
    claim_id: uuid.UUID
    claim_key: str = Field(min_length=1, max_length=160)
    boundary: str = Field(min_length=1, max_length=64)
    verbatim_text: str = Field(min_length=1)
    source_count: int = Field(ge=1)


class SlidePlanningStyleConstraints(FrozenDomainModel):
    allowed_layout_tokens: tuple[LayoutToken, ...] = Field(min_length=1)
    preferred_layout_tokens: tuple[LayoutToken, ...] = ()
    max_pages: int = Field(ge=1, le=30)
    max_components_per_page: int = Field(ge=1, le=6)


class ComponentGeometry(FrozenDomainModel):
    x: int = Field(ge=0, le=10_000)
    y: int = Field(ge=0, le=10_000)
    width: int = Field(gt=0, le=10_000)
    height: int = Field(gt=0, le=10_000)


class PositionedComponent(FrozenDomainModel):
    slot_name: str = Field(min_length=1, max_length=64)
    geometry: ComponentGeometry
    z_index: int = Field(ge=0, le=100)
    text_style_token: Literal["display", "heading", "body", "evidence"]
    overflow_policy: Literal["fit", "clip"] = "fit"
    component: SemanticComponent


class PositionedSlide(FrozenDomainModel):
    slide_id: uuid.UUID
    layout_token: LayoutToken
    components: tuple[PositionedComponent, ...] = Field(min_length=1)


class PositionedPresentationSpec(FrozenDomainModel):
    schema_version: Literal["positioned-spec-v1"] = "positioned-spec-v1"
    presentation_id: uuid.UUID
    slides: tuple[PositionedSlide, ...] = Field(min_length=1)


class EvidenceGuardSnapshot(FrozenDomainModel):
    component_fingerprints: dict[uuid.UUID, str]
    component_claim_ids: dict[uuid.UUID, tuple[uuid.UUID, ...]]


class Palette(StrictDomainModel):
    primary: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    secondary: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    background: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    foreground: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class Typography(StrictDomainModel):
    heading_font: str = Field(min_length=1, max_length=100)
    body_font: str = Field(min_length=1, max_length=100)
    base_size_px: int = Field(ge=10, le=48)
    scale_ratio: float = Field(gt=1, le=2)


class SpacingGrid(StrictDomainModel):
    base_unit_px: int = Field(ge=2, le=32)
    slide_padding_units: int = Field(ge=1, le=20)
    component_gap_units: int = Field(ge=0, le=20)


class VisualStyleProfileData(StrictDomainModel):
    palette: Palette
    typography: Typography
    spacing_grid: SpacingGrid
    layout_grammar: list[str] = Field(min_length=1)


class NarrativeStyleProfileData(StrictDomainModel):
    tone: Literal["formal", "consultative", "concise", "technical"]
    headline_pattern: str = Field(min_length=1, max_length=300)
    body_rules: list[str] = Field(min_length=1)
    forbidden_patterns: list[str] = Field(default_factory=list)
