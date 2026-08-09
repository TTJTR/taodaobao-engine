import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CreateReferenceDeckRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=300)
    storage_key: str = Field(min_length=1, max_length=1000)
    file_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    mime_type: Literal["application/vnd.openxmlformats-officedocument.presentationml.presentation"]
    size_bytes: int = Field(gt=0, le=100 * 1024 * 1024)
    source_url: str | None = Field(default=None, max_length=2000)


class GenerateStyleProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    reference_deck_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)


class UpdateStyleProfileRequest(BaseModel):
    expected_version: int = Field(ge=1)
    visual_json: dict | None = None
    narrative_json: dict | None = None
    conflict_resolutions: dict[str, str] = Field(default_factory=dict)


class ConfirmStyleProfileRequest(BaseModel):
    expected_version: int = Field(ge=1)


class CreatePresentationRequest(BaseModel):
    style_profile_id: uuid.UUID
    mode: Literal["strict", "balanced", "brand_only"] = "balanced"
    audience: str = Field(min_length=1, max_length=64)
    output: list[Literal["html", "pdf"]] = Field(default_factory=lambda: ["html"])
    language: Literal["zh-CN", "en-US"] = "zh-CN"

    @model_validator(mode="after")
    def output_must_be_unique(self) -> "CreatePresentationRequest":
        if not self.output or len(self.output) != len(set(self.output)):
            raise ValueError("output must contain one or more unique formats")
        return self


class UpdatePresentationBlockRequest(BaseModel):
    expected_version: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=20_000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    image_key: str | None = Field(default=None, max_length=1000)
    position: int | None = Field(default=None, ge=0)
    locked: bool | None = None
    claim_id: str | None = Field(default=None, max_length=160)


class RegeneratePresentationRequest(BaseModel):
    expected_version: int = Field(ge=1)
    block_ids: list[str] = Field(default_factory=list, max_length=100)


class ExportPresentationRequest(BaseModel):
    expected_version: int = Field(ge=1)
    export_type: Literal["html", "pdf"]
