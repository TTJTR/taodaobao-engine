import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import User
from app.db.repositories import UserRepository
from app.integrations.protocols import FeishuAdapter


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        feishu_adapter: FeishuAdapter,
        workspace_id: uuid.UUID,
        user_repository: UserRepository | None = None,
    ) -> None:
        self.session = session
        self.feishu_adapter = feishu_adapter
        self.users = user_repository or UserRepository(session, workspace_id)

    async def handle_feishu_callback(self, code: str, redirect_uri: str) -> User:
        try:
            user_info = await self.feishu_adapter.exchange_code(code, redirect_uri)
        except ValueError as exc:
            raise AppError(
                ErrorCode.FEISHU_AUTH_EXPIRED,
                "飞书授权码无效或已过期",
                status_code=401,
                retryable=False,
            ) from exc
        except Exception as exc:
            raise AppError(
                ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE,
                "飞书服务暂时不可用",
                status_code=503,
                retryable=True,
            ) from exc

        user = await self.users.get_by_feishu_user_id(user_info.feishu_user_id)
        token_values = {
            "feishu_tenant_key": user_info.tenant_key,
            "feishu_access_token": user_info.access_token,
            "feishu_refresh_token": user_info.refresh_token,
            "feishu_token_expires_at": user_info.expires_at,
        }
        token_values = {key: value for key, value in token_values.items() if value is not None}
        if user is None:
            user = await self.users.create(
                feishu_user_id=user_info.feishu_user_id,
                name=user_info.name,
                avatar=user_info.avatar,
                **token_values,
            )
        else:
            user = await self.users.update(
                user,
                name=user_info.name,
                avatar=user_info.avatar,
                **token_values,
            )

        await self.session.commit()
        await self.session.refresh(user)
        return user
