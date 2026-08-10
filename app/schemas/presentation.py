import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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


SemanticComponent = Annotated[
    TitleComponent | KeyMessageComponent | EvidenceCardComponent,
    Field(discriminator="component_type"),
]

LayoutToken = Literal["cover", "title_body", "two_column", "three_cards", "evidence_grid"]


class SlideSchema(StrictDomainModel):
    slide_id: uuid.UUID
    layout_token: LayoutToken
    components: list[SemanticComponent] = Field(min_length=1)


class PresentationSpecData(StrictDomainModel):
    schema_version: str = Field(min_length=1, max_length=32)
    presentation_id: uuid.UUID
    slides: list[SlideSchema] = Field(min_length=1)


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
    component_claim_ids: dict[uuid.UUID, uuid.UUID]


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
