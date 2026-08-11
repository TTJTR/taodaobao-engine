from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StyleFeatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CanvasFeature(StyleFeatureModel):
    width_emu: int = Field(gt=0)
    height_emu: int = Field(gt=0)
    aspect_ratio: float = Field(gt=0.5, lt=3)


class NormalizedGeometry(StyleFeatureModel):
    x: int = Field(ge=0, le=10_000)
    y: int = Field(ge=0, le=10_000)
    width: int = Field(gt=0, le=10_000)
    height: int = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def validate_bounds(self) -> "NormalizedGeometry":
        if self.x + self.width > 10_000 or self.y + self.height > 10_000:
            raise ValueError("normalized geometry exceeds the canvas")
        return self


class ColorSample(StyleFeatureModel):
    color: str = Field(pattern=r"^#[0-9A-F]{6}$")
    role: Literal["theme", "fill", "text", "line", "background"]
    frequency: int = Field(ge=1)
    page_coverage: float = Field(ge=0, le=1)


class FontSample(StyleFeatureModel):
    family: str = Field(min_length=1, max_length=100)
    size_pt: float = Field(gt=0, le=200)
    weight: Literal["regular", "semibold", "bold"] = "regular"
    role: Literal["display", "title", "subtitle", "body", "evidence", "footnote"]
    frequency: int = Field(ge=1)


class ShapeSample(StyleFeatureModel):
    kind: Literal["sp", "graphicFrame", "pic"]
    geometry: NormalizedGeometry
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    line: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    line_width_emu: int | None = Field(default=None, ge=0)
    radius_hint: Literal["square", "soft", "round", "unknown"] = "unknown"


class PageSample(StyleFeatureModel):
    page_index: int = Field(ge=1)
    boxes: tuple[NormalizedGeometry, ...]
    title_band: bool
    column_count: int = Field(ge=1, le=4)
    card_grid: bool
    image_region: bool
    density: float = Field(ge=0, le=1)


class MasterPlaceholder(StyleFeatureModel):
    role: str = Field(min_length=1, max_length=64)
    geometry: NormalizedGeometry | None = None


class MasterLayoutFeature(StyleFeatureModel):
    name: str = Field(min_length=1, max_length=160)
    placeholders: tuple[MasterPlaceholder, ...]


class StyleFeatureSet(StyleFeatureModel):
    schema_version: Literal["style-feature-set-v1"] = "style-feature-set-v1"
    canvas: CanvasFeature
    color_samples: tuple[ColorSample, ...]
    font_samples: tuple[FontSample, ...]
    shape_samples: tuple[ShapeSample, ...]
    page_samples: tuple[PageSample, ...]
    master_layouts: tuple[MasterLayoutFeature, ...]
    source_mode: Literal["theme_only", "sanitized_visual"]

