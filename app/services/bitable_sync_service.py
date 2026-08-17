import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Capability,
    CustomerProfile,
    Experience,
    ResearchTask,
    Source,
    TenderDocument,
)
from app.integrations.protocols import FeishuAdapter, FeishuBitable


class BitableSyncService:
    """One-way export of the current workspace state to a real Feishu Bitable."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        feishu: FeishuAdapter,
        access_token: str | None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.feishu = feishu
        self.access_token = access_token

    async def sync_daily(self) -> dict[str, Any]:
        target = self._load_target()
        created = False
        if target is None:
            target = await self.feishu.create_bitable("淘到宝·今日战情", self.access_token)
            self._save_target(target)
            created = True
        records = await self._records()
        count = await self.feishu.append_bitable_records(
            target.app_token,
            target.table_id,
            records,
            self.access_token,
        )
        self._save_target(target, record_count=count)
        return {
            "status": "synced",
            "created": created,
            "record_count": count,
            "url": target.url,
            "app_token": target.app_token,
            "table_id": target.table_id,
            "synced_at": datetime.now(UTC).isoformat(),
        }

    def sync_status(self) -> dict[str, Any]:
        """Return the persisted real target without causing another write."""
        target = self._load_target()
        if target is None:
            return {"configured": False}
        path = self._target_path()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            record_count = saved.get("record_count")
            persisted_at = saved.get("synced_at")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            record_count = None
            persisted_at = None
        synced_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        return {
            "configured": True,
            "status": "connected",
            "record_count": record_count,
            "url": target.url,
            "app_token": target.app_token,
            "table_id": target.table_id,
            "synced_at": persisted_at or synced_at,
        }

    async def _records(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        profiles = await self._all(CustomerProfile)
        sources = await self._all(Source)
        experiences = await self._all(Experience)
        capabilities = await self._all(Capability)
        research_tasks = await self._all(ResearchTask)
        tenders = await self._all(TenderDocument)
        for item in profiles:
            profile = item.profile or {}
            rows.append(
                self._row(
                    item.customer_name,
                    "客户画像",
                    item.status,
                    profile.get("background") or profile.get("current_problem") or "画像待补充",
                    item.id,
                    item.updated_at,
                )
            )
        for item in sources:
            rows.append(
                self._row(
                    item.title,
                    "资料来源",
                    item.status,
                    f"作者：{item.author or '待同步'}；用途：{self._value(item.purpose)}",
                    item.id,
                    item.updated_at,
                )
            )
        for item in experiences:
            data = item.data or {}
            rows.append(
                self._row(
                    data.get("name") or "未命名经验",
                    "历史经验",
                    item.review_status,
                    data.get("solution") or data.get("applicable_problem") or "经验待补充",
                    item.id,
                    item.updated_at,
                )
            )
        for item in capabilities:
            data = item.data or {}
            rows.append(
                self._row(
                    data.get("name") or "未命名能力",
                    "原子能力",
                    item.review_status,
                    data.get("description") or data.get("solution") or "能力待补充",
                    item.id,
                    item.updated_at,
                )
            )
        for item in research_tasks:
            rows.append(
                self._row(
                    item.title,
                    "Deep Research",
                    item.status,
                    f"{item.question}；进度 {item.progress}%",
                    item.id,
                    item.updated_at,
                )
            )
        for item in tenders:
            rows.append(
                self._row(
                    item.title,
                    "招标项目",
                    item.status,
                    item.source_filename or "招标正文",
                    item.id,
                    item.updated_at,
                )
            )
        return rows[:200]

    async def _all(self, model) -> list[Any]:
        return list(
            (
                await self.session.scalars(
                    select(model)
                    .where(
                        model.workspace_id == self.workspace_id,
                        model.is_deleted.is_(False),
                    )
                    .order_by(model.updated_at.desc())
                )
            ).all()
        )

    @classmethod
    def _row(cls, title, kind, status, summary, source_id, updated_at) -> dict[str, str]:
        return {
            "主题": str(title)[:500],
            "类型": str(kind),
            "状态": cls._value(status),
            "摘要": str(summary)[:2000],
            "来源ID": str(source_id),
            "更新时间": updated_at.astimezone(UTC).isoformat(),
        }

    @staticmethod
    def _value(value: Any) -> str:
        return str(getattr(value, "value", value))

    def _target_path(self) -> Path:
        return settings.integration_state_dir / f"bitable-{self.workspace_id}.json"

    def _load_target(self) -> FeishuBitable | None:
        path = self._target_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return FeishuBitable(
                app_token=str(data["app_token"]),
                table_id=str(data["table_id"]),
                url=str(data["url"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _save_target(self, target: FeishuBitable, record_count: int | None = None) -> None:
        path = self._target_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "app_token": target.app_token,
                    "table_id": target.table_id,
                    "url": target.url,
                    "record_count": record_count,
                    "synced_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
