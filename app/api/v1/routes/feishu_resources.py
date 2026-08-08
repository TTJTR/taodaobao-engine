from typing import Literal

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, FeishuAdapterDependency
from app.core.responses import success_response
from app.schemas.v1 import FeishuResourceRead

router = APIRouter()


@router.get("/resources")
async def list_feishu_resources(
    request: Request,
    current_user: CurrentUser,
    adapter: FeishuAdapterDependency,
    resource_type: Literal["document", "minute"] = Query(alias="type"),
    page_token: str | None = Query(default=None, max_length=512),
) -> dict[str, object]:
    items, next_page_token = await adapter.list_resources(
        resource_type, current_user.feishu_access_token, page_token
    )
    return success_response(
        request,
        {
            "items": [
                FeishuResourceRead.model_validate(item, from_attributes=True).model_dump(
                    mode="json"
                )
                for item in items
            ],
            "next_page_token": next_page_token,
        },
    )
