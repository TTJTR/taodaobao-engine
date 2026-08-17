import json
import uuid

from app.core.config import settings
from app.services.bitable_sync_service import BitableSyncService


def test_sync_status_restores_real_target_without_writing(tmp_path, monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    monkeypatch.setattr(settings, "integration_state_dir", tmp_path)
    target = tmp_path / f"bitable-{workspace_id}.json"
    target.write_text(
        json.dumps(
            {
                "app_token": "base-real",
                "table_id": "table-real",
                "url": "https://feishu.cn/base/base-real",
                "record_count": 38,
                "synced_at": "2026-08-16T01:25:25+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = BitableSyncService(None, workspace_id, None, None).sync_status()

    assert result["configured"] is True
    assert result["record_count"] == 38
    assert result["url"] == "https://feishu.cn/base/base-real"


def test_sync_status_reports_unconfigured_without_target(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "integration_state_dir", tmp_path)

    result = BitableSyncService(None, uuid.uuid4(), None, None).sync_status()

    assert result == {"configured": False}
