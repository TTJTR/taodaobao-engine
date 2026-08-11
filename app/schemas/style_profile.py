import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.presentation.style.features import NormalizedGeometry


class StyleProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColorTokens(StyleProfileModel):
    canvas: str = Field(pattern=r"^#[0-9A-F]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-F]{6}$")
    primary: str = Field(pattern=r"^#[0-9A-F]{6}$")
    secondary: str = Field(pattern=r"^#[0-9A-F]{6}$")
    accent: str = Field(pattern=r"^#[0-9A-F]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-F]{6}$")
    text_muted: str = Field(pattern=r"^#[0-9A-F]{6}$")
    border: str = Field(pattern=r"^#[0-9A-F]{6}$")


class TypographyToken(StyleProfileModel):
    font_family: str = Field(min_length=1, max_length=100)
    size_pt: float = Field(ge=10, le=48)
    weight: Literal["regular", "semibold", "bold"]
    line_spacing: float = Field(ge=1, le=2)
    color_token: Literal["text_primary", "text_muted", "primary", "accent"]


class TypographyTokens(StyleProfileModel):
    display: TypographyToken
    title: TypographyToken
    subtitle: TypographyToken
    body: TypographyToken
    evidence: TypographyToken
    footnote: TypographyToken


class SpacingTokens(StyleProfileModel):
    page_margin: int = Field(ge=300, le=1_500)
    section_gap: int = Field(ge=100, le=1_000)
    component_gap: int = Field(ge=100, le=1_000)
    card_padding: int = Field(ge=120, le=800)


class ShapeTokens(StyleProfileModel):
    radius: int = Field(ge=0, le=400)
    border_width: int = Field(ge=0, le=80)
    border_style: Literal["none", "solid"]
    shadow: Literal["none", "soft"]
    surface_fill: Literal["canvas", "surface", "primary", "secondary"]


class DecorationTokens(StyleProfileModel):
    title_rule: bool
    accent_bar: bool
    page_number: bool
    header_footer: bool


class DesignTokens(StyleProfileModel):
    colors: ColorTokens
    typography: TypographyTokens
    spacing: SpacingTokens
    shape: ShapeTokens
    decoration: DecorationTokens


class ComponentSkin(StyleProfileModel):
    token: str = Field(min_length=1, max_length=64)
    applies_to: tuple[str, ...] = Field(min_length=1)
    fill_token: Literal["canvas", "surface", "primary", "secondary"]
    border_token: Literal["border", "primary", "secondary", "accent"]
    border_width: int = Field(ge=0, le=80)
    radius: int = Field(ge=0, le=400)
    padding: int = Field(ge=100, le=800)
    heading_style: Literal["display", "title", "subtitle"]
    body_style: Literal["body", "evidence", "footnote"]
    alignment: Literal["left", "center", "right"]
    vertical_alignment: Literal["top", "middle", "bottom"]
    accent_placement: Literal["none", "top", "left", "bottom"]


class ArchetypeCapacity(StyleProfileModel):
    max_characters: int = Field(ge=1, le=8_000)
    max_lines: int = Field(ge=1, le=40)
    min_font_pt: float = Field(ge=10, le=48)


class ArchetypeSlot(StyleProfileModel):
    role: str = Field(min_length=1, max_length=64)
    geometry: NormalizedGeometry
    allowed_components: tuple[str, ...] = Field(min_length=1)
    capacity: ArchetypeCapacity


class LayoutArchetype(StyleProfileModel):
    archetype_token: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    frequency: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    slots: tuple[ArchetypeSlot, ...] = Field(min_length=1)
    preferred_skin_tokens: tuple[str, ...] = Field(min_length=1)
    fallback_archetype: str

    @model_validator(mode="after")
    def validate_unique_roles(self) -> "LayoutArchetype":
        roles = [slot.role for slot in self.slots]
        if len(roles) != len(set(roles)):
            raise ValueError("archetype slot roles must be unique")
        return self


class ConfidenceReport(StyleProfileModel):
    overall: float = Field(ge=0, le=1)
    color: float = Field(ge=0, le=1)
    typography: float = Field(ge=0, le=1)
    layout: float = Field(ge=0, le=1)
    warnings: tuple[str, ...] = ()


class VisualStyleProfileV2(StyleProfileModel):
    schema_version: Literal["style-profile-v2"] = "style-profile-v2"
    profile_id: uuid.UUID
    design_tokens: DesignTokens
    component_skins: tuple[ComponentSkin, ...] = Field(min_length=1)
    layout_archetypes: tuple[LayoutArchetype, ...] = Field(min_length=1)
    confidence_report: ConfidenceReport
    compiler_version: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_tokens(self) -> "VisualStyleProfileV2":
        skin_tokens = [skin.token for skin in self.component_skins]
        layout_tokens = [item.archetype_token for item in self.layout_archetypes]
        if len(skin_tokens) != len(set(skin_tokens)):
            raise ValueError("component skin tokens must be unique")
        if len(layout_tokens) != len(set(layout_tokens)):
            raise ValueError("layout archetype tokens must be unique")
        known_skins = set(skin_tokens)
        if any(
            token not in known_skins
            for archetype in self.layout_archetypes
            for token in archetype.preferred_skin_tokens
        ):
            raise ValueError("layout archetype references an unknown component skin")
        return self
