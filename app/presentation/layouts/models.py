from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.presentation import ComponentGeometry, LayoutToken

ComponentType = Literal[
    "title",
    "key_message",
    "evidence_card",
    "metric",
    "comparison",
    "timeline",
    "process",
    "source_list",
]


class LayoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Capacity(LayoutModel):
    max_components: int = Field(ge=1, le=6)
    max_characters: int = Field(ge=1, le=8_000)
    max_lines: int = Field(ge=1, le=40)
    min_font_px: int = Field(ge=12, le=48)


class Slot(LayoutModel):
    name: str = Field(min_length=1, max_length=64)
    geometry: ComponentGeometry
    allowed_component_types: tuple[ComponentType, ...] = Field(min_length=1)
    capacity: Capacity
    text_style_token: Literal["display", "heading", "body", "evidence"]
    z_index: int = Field(default=1, ge=0, le=100)


class LayoutTemplate(LayoutModel):
    token: LayoutToken
    slots: tuple[Slot, ...] = Field(min_length=1)
    fallback_token: LayoutToken | None = None

    @model_validator(mode="after")
    def validate_unique_slots(self) -> "LayoutTemplate":
        names = [slot.name for slot in self.slots]
        if len(names) != len(set(names)):
            raise ValueError("layout slot names must be unique")
        return self
