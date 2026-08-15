import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.routes.tenders import _requirement
from app.core.errors import AppError, ErrorCode
from app.main import app
from app.services.tender_service import _persist_tender_artifact, _safe_upload_filename


def test_requirement_response_handles_fresh_string_status() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tender_id=uuid.uuid4(),
        sequence=1,
        requirement_text="系统必须支持私有化部署。",
        category="deployment",
        mandatory=True,
        source_location={"content_kind": "table", "paragraph": 1},
        version=1,
        status="ai_draft",
        acceptance_condition=None,
        constraints={},
        metrics=[],
        ambiguities=[],
        recommended_action=None,
        confirmed_by_id=None,
        confirmed_at=None,
    )

    assert _requirement(row)["status"] == "ai_draft"


def test_tender_upload_route_is_incremental_and_binary() -> None:
    operation = app.openapi()["paths"]["/api/v1/tenders/file-uploads"]["post"]

    assert operation["operationId"] == "upload_tender_file_api_v1_tenders_file_uploads_post"
    assert {parameter["name"] for parameter in operation["parameters"]} >= {
        "title",
        "X-File-Name",
        "Idempotency-Key",
    }
    assert set(operation["requestBody"]["content"]) >= {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
    }


def test_tender_artifact_is_persisted_below_workspace_root(tmp_path: Path) -> None:
    payload = b"%PDF-1.7\nminimal"
    workspace_id = uuid.uuid4()
    digest = hashlib.sha256(payload).hexdigest()

    first = _persist_tender_artifact(
        payload,
        workspace_id=workspace_id,
        content_sha256=digest,
        filename="tender.pdf",
        storage_root=tmp_path,
    )
    second = _persist_tender_artifact(
        payload,
        workspace_id=workspace_id,
        content_sha256=digest,
        filename="tender.pdf",
        storage_root=tmp_path,
    )

    assert first == second
    assert first.read_bytes() == payload
    assert first.name == f"{digest}.pdf"
    assert first.is_relative_to((tmp_path / str(workspace_id)).resolve())


@pytest.mark.parametrize(
    "filename",
    ["../tender.pdf", "folder/tender.pdf", "folder\\tender.pdf", ""],
)
def test_tender_upload_rejects_unsafe_filename(filename: str) -> None:
    with pytest.raises(AppError) as caught:
        _safe_upload_filename(filename)

    assert caught.value.code == ErrorCode.TENDER_FILE_UNSAFE
