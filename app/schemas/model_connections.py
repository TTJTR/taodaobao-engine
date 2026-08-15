from typing import Literal

from pydantic import BaseModel, Field

ModelCapability = Literal["ai", "interactive-html"]
ModelProvider = Literal["dashscope", "deepseek"]


class ConfigureModelConnectionRequest(BaseModel):
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_key: str = Field(min_length=8, max_length=4096)
