import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.presentation import ComponentGeometry


class RenderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ContentOrigin = Literal[
    "ledger_verbatim",
    "ledger_label",
    "user_authored",
    "system_label",
]


class FactTrace(RenderModel):
    claim_id: uuid.UUID
    claim_key: str = Field(min_length=1, max_length=160)
    evidence_ids: tuple[uuid.UUID, ...] = Field(min_length=1)
    source_ids: tuple[uuid.UUID, ...] = Field(min_length=1)
    content_mode: Literal["verbatim", "label_only"]


class UserTrace(RenderModel):
    snapshot_id: uuid.UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SystemLabelTrace(RenderModel):
    token: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,95}$")
    catalog_version: str = Field(min_length=1, max_length=32)


class TextProvenance(RenderModel):
    content_origin: ContentOrigin
    fact_trace: FactTrace | None = None
    user_trace: UserTrace | None = None
    system_label_trace: SystemLabelTrace | None = None

    @model_validator(mode="after")
    def validate_origin_trace(self) -> "TextProvenance":
        TextRun(
            run_id=uuid.uuid4(),
            text="provenance-validation",
            content_origin=self.content_origin,
            fact_trace=self.fact_trace,
            user_trace=self.user_trace,
            system_label_trace=self.system_label_trace,
        )
        return self


class TextRun(RenderModel):
    run_id: uuid.UUID
    text: str = Field(min_length=1, max_length=8_000)
    content_origin: ContentOrigin
    fact_trace: FactTrace | None = None
    user_trace: UserTrace | None = None
    system_label_trace: SystemLabelTrace | None = None

    @model_validator(mode="after")
    def validate_origin_trace(self) -> "TextRun":
        traces = {
            "fact": self.fact_trace is not None,
            "user": self.user_trace is not None,
            "system": self.system_label_trace is not None,
        }
        expected = {
            "ledger_verbatim": "fact",
            "ledger_label": "fact",
            "user_authored": "user",
            "system_label": "system",
        }[self.content_origin]
        if sum(traces.values()) != 1 or not traces[expected]:
            raise ValueError("content_origin requires exactly one matching trace")
        if self.fact_trace is not None:
            expected_mode = (
                "verbatim" if self.content_origin == "ledger_verbatim" else "label_only"
            )
            if self.fact_trace.content_mode != expected_mode:
                raise ValueError("fact trace content_mode does not match content_origin")
        return self


class LineBox(RenderModel):
    text: str
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(ge=0)
    height: float = Field(gt=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> "LineBox":
        if self.end_offset < self.start_offset:
            raise ValueError("line end_offset must not precede start_offset")
        return self


class TextLayout(RenderModel):
    engine_version: Literal["text-layout-v1", "text-layout-v2"] = "text-layout-v2"
    measurement_method: Literal["opentype", "conservative"] = "conservative"
    requested_font: str = Field(min_length=1, max_length=100)
    resolved_font: str = Field(min_length=1, max_length=100)
    font_file_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    font_size: float = Field(ge=10, le=96)
    line_height: float = Field(gt=0, le=160)
    line_breaks: tuple[int, ...]
    line_boxes: tuple[LineBox, ...] = Field(min_length=1)
    overflowed: bool = False


class TextNode(RenderModel):
    node_type: Literal["text"] = "text"
    node_id: uuid.UUID
    component_id: uuid.UUID
    geometry: ComponentGeometry
    z_index: int = Field(ge=0, le=100)
    fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    font_weight: Literal["regular", "semibold", "bold"]
    text_align: Literal["left", "center", "right"] = "left"
    runs: tuple[TextRun, ...] = Field(min_length=1)
    layout: TextLayout

    @model_validator(mode="after")
    def validate_layout_text(self) -> "TextNode":
        run_text = "".join(run.text for run in self.runs)
        line_text = "".join(line.text for line in self.layout.line_boxes)
        if run_text != line_text:
            raise ValueError("TextLayout must preserve TextRun content exactly")
        return self


class ShapeNode(RenderModel):
    node_type: Literal["shape"] = "shape"
    node_id: uuid.UUID
    geometry: ComponentGeometry
    z_index: int = Field(ge=0, le=100)
    fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: float = Field(default=0, ge=0, le=20)
    corner_radius: float = Field(default=0, ge=0, le=100)
    opacity: float = Field(default=1, ge=0, le=1)
    shadow_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    shadow_blur: float = Field(default=0, ge=0, le=40)
    shadow_offset_x: float = Field(default=0, ge=-40, le=40)
    shadow_offset_y: float = Field(default=0, ge=-40, le=40)


RenderNode = Annotated[TextNode | ShapeNode, Field(discriminator="node_type")]


class CanvasSpec(RenderModel):
    width: int = Field(default=1600, gt=0)
    height: int = Field(default=900, gt=0)
    aspect_ratio: Literal["16:9"] = "16:9"
    coordinate_unit: Literal["px"] = "px"
    scale_mode: Literal["contain"] = "contain"


class RenderSlide(RenderModel):
    slide_id: uuid.UUID
    layers: tuple[RenderNode, ...] = Field(min_length=1)


class RenderIR(RenderModel):
    schema_version: Literal["render-ir-v1"] = "render-ir-v1"
    renderer_contract_version: Literal["renderer-contract-v1"] = "renderer-contract-v1"
    presentation_id: uuid.UUID
    canvas: CanvasSpec = Field(default_factory=CanvasSpec)
    positioned_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_style_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    slides: tuple[RenderSlide, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "RenderIR":
        slide_ids = [slide.slide_id for slide in self.slides]
        node_ids = [node.node_id for slide in self.slides for node in slide.layers]
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("RenderIR slide_ids must be unique")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("RenderIR node_ids must be unique")
        return self
