import csv
import hashlib
import io
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.api.v1.routes.tenders import _matrix_csv
from app.core.errors import AppError, ErrorCode
from app.main import app
from app.services.tender_service import (
    TenderService,
    _evidence_link,
    _validate_document_location,
    extract_requirement_candidate,
)


class _MatrixListResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class _MatrixListSession:
    def __init__(self, rows, total: int) -> None:
        self.rows = rows
        self.total = total
        self.executed = []
        self.scalar_statement = None

    async def execute(self, statement):
        self.executed.append(statement)
        return _MatrixListResult(self.rows)

    async def scalar(self, statement):
        self.scalar_statement = statement
        return self.total


class _SnapshotSession:
    def __init__(self) -> None:
        self.added = []

    def add(self, row) -> None:
        self.added.append(row)


class _FilteredMatrixSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.statement = None

    async def scalar(self, _statement):
        return SimpleNamespace(id=uuid.uuid4())

    async def execute(self, statement):
        self.statement = statement
        return _MatrixListResult(self.rows)


class _BatchScalars:
    def __init__(self, rows) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _BatchSession(_SnapshotSession):
    def __init__(self, rows) -> None:
        super().__init__()
        self.rows = rows
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = []

    async def scalars(self, _statement):
        return _BatchScalars(self.rows)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        self.refreshes.append(row)

    async def rollback(self):
        self.rollbacks += 1


def location(text: str) -> dict:
    return {
        "kind": "pdf_page",
        "schema_version": "document-location-v1",
        "quote_hash": hashlib.sha256(text.encode()).hexdigest(),
        "page": 3,
        "bounding_box": [10.0, 20.0, 100.0, 140.0],
    }


def test_requirement_location_is_accepted_without_rewriting() -> None:
    original = location("供应商必须提供三年实施经验。")

    _validate_document_location(original)

    assert original["page"] == 3
    assert original["bounding_box"] == [10.0, 20.0, 100.0, 140.0]


@pytest.mark.parametrize("marker", ["★", "必须", "否则拒绝"])
def test_requirement_candidate_marks_hard_constraints_as_mandatory(marker: str) -> None:
    text = f"{marker} 供应商应提供三年内同类项目案例。"
    candidate = extract_requirement_candidate(
        {"text": text, "node_type": "paragraph", "location": location(text)}
    )

    assert candidate is not None
    assert candidate.is_mandatory is True
    assert "强制条款" in (candidate.recommended_action or "")


def test_requirement_candidate_extracts_numeric_and_time_metrics() -> None:
    text = "系统必须支持10万并发，响应时间不超过200ms，并在30日内完成交付，预算50万元。"
    candidate = extract_requirement_candidate(
        {"text": text, "node_type": "paragraph", "location": location(text)}
    )

    assert candidate is not None
    values = {(item["type"], item["value"]) for item in candidate.metrics}
    assert ("performance", "10万并发") in values
    assert ("performance", "200ms") in values
    assert ("duration", "30日内") in values
    assert ("amount", "50万元") in values


def test_requirement_candidate_inherits_document_location_without_drift() -> None:
    text = "投标人应当提供符合等保三级要求的数据安全实施方案。"
    original = location(text)
    before = deepcopy(original)
    candidate = extract_requirement_candidate(
        {"text": text, "node_type": "paragraph", "location": original}
    )

    assert candidate is not None
    assert candidate.source_location == before
    assert original == before
    assert candidate.source_location is not original


@pytest.mark.asyncio
async def test_requirement_version_snapshot_keeps_structured_manual_fields() -> None:
    session = _SnapshotSession()
    service = TenderService(session, uuid.uuid4(), uuid.uuid4())
    source_location = location("系统必须支持10万并发。")
    row = SimpleNamespace(
        id=uuid.uuid4(),
        version=2,
        requirement_text="系统必须支持10万并发。",
        category="technical",
        mandatory=True,
        acceptance_condition="压测达到10万并发",
        constraints={},
        metrics=[{"type": "performance", "value": "10万并发"}],
        ambiguities=["需确认压测模型"],
        recommended_action="需技术负责人复核",
        status=SimpleNamespace(value="edited"),
        source_location=source_location,
    )

    await service._snapshot_requirement(row, "edited")
    version = session.added[0]

    assert version.requirement_snapshot["metrics"] == row.metrics
    assert version.requirement_snapshot["recommended_action"] == row.recommended_action
    assert version.source_location == source_location


@pytest.mark.parametrize(
    "invalid",
    [None, {}, {"kind": "pdf_page", "schema_version": "document-location-v1", "page": 1}],
)
def test_requirement_without_verifiable_location_is_rejected(invalid) -> None:
    with pytest.raises(AppError) as caught:
        _validate_document_location(invalid)

    assert caught.value.code == ErrorCode.EVIDENCE_LOCATION_INVALID


def test_internal_evidence_link_drops_external_context_fields() -> None:
    link = _evidence_link(
        {
            "id": "exp-1",
            "source_id": "source-1",
            "source_snapshot": {"permission_valid": True},
            "match_reasons": ["lexical_hits=1/1"],
            "external_intelligence_snapshot_id": "must-not-leak",
        }
    )

    assert set(link) == {"asset_id", "source_id", "source_snapshot", "match_reasons"}


def test_explicit_internal_evidence_link_keeps_source_identity() -> None:
    link = _evidence_link(
        {
            "id": "capability-1",
            "source_id": "source-1",
            "source_snapshot": {"available": True},
            "match_reasons": ["explicitly_selected_evidence"],
        }
    )

    assert link["asset_id"] == "capability-1"
    assert link["source_id"] == "source-1"


def test_missing_evidence_cannot_be_approved_and_ai_draft_is_immutable() -> None:
    item = SimpleNamespace(version=1, risk_flags=["missing_evidence"], ai_draft="原始 AI 草稿")

    TenderService._check_version(item, 1)
    with pytest.raises(AppError) as caught:
        TenderService._validate_review_action(item, "approve", None)

    assert caught.value.code == ErrorCode.RESPONSE_EVIDENCE_INVALID
    assert item.ai_draft == "原始 AI 草稿"


def test_optimistic_lock_rejects_stale_response_version() -> None:
    item = SimpleNamespace(version=3)

    with pytest.raises(AppError) as caught:
        TenderService._check_version(item, 2)

    assert caught.value.code == ErrorCode.RESPONSE_VERSION_CONFLICT


def test_optimistic_lock_rejects_stale_requirement_version() -> None:
    requirement = SimpleNamespace(version=4)

    with pytest.raises(AppError) as caught:
        TenderService._check_requirement_version(requirement, 3)

    assert caught.value.code == ErrorCode.RESPONSE_VERSION_CONFLICT


@pytest.mark.asyncio
async def test_batch_review_is_atomic_and_versions_every_item(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    matrix_id = uuid.uuid4()
    user_id = uuid.uuid4()
    items = [
        SimpleNamespace(
            id=uuid.uuid4(),
            matrix_id=matrix_id,
            version=1,
            risk_flags=[],
            response_text="draft",
            ai_draft="draft",
            current_answer="draft",
            evidence_status=SimpleNamespace(value="supported"),
            evidence_refs=[],
            risks=[],
            internal_exp_links=[],
            internal_cap_links=[],
            external_ctx_links=[],
            review_status="pending",
            reviewer_id=None,
            review_note=None,
            approved_at=None,
        )
        for _ in range(2)
    ]
    session = _BatchSession(sorted(items, key=lambda item: str(item.id)))
    service = TenderService(session, workspace_id, user_id)

    async def get_matrix(_model, received_id):
        assert received_id == matrix_id
        return SimpleNamespace(id=matrix_id)

    monkeypatch.setattr(service, "_get", get_matrix)
    versions = {str(item.id): item.version for item in items}
    result = await service.batch_review_items(
        matrix_id, [item.id for item in items], "needs_evidence", versions, "batch"
    )

    assert len(result) == 2
    assert all(item.review_status == "needs_evidence" for item in result)
    assert all(item.version == 2 for item in result)
    assert len(session.added) == 2
    assert session.commits == 1
    assert len(session.refreshes) == 2


@pytest.mark.asyncio
async def test_batch_review_version_conflict_does_not_commit(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    matrix_id = uuid.uuid4()
    item = SimpleNamespace(id=uuid.uuid4(), matrix_id=matrix_id, version=3, risk_flags=[])
    session = _BatchSession([item])
    service = TenderService(session, workspace_id, uuid.uuid4())

    async def get_matrix(_model, _received_id):
        return SimpleNamespace(id=matrix_id)

    monkeypatch.setattr(service, "_get", get_matrix)
    with pytest.raises(AppError) as caught:
        await service.batch_review_items(matrix_id, [item.id], "approve", {str(item.id): 2}, None)

    assert caught.value.code == ErrorCode.RESPONSE_VERSION_CONFLICT
    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.added == []


def test_matrix_csv_contains_review_and_evidence_fields() -> None:
    item = SimpleNamespace(
        ai_draft="AI draft",
        current_answer="Human answer",
        internal_exp_links=[{"asset_id": "exp-1"}],
        internal_cap_links=[{"asset_id": "cap-1"}],
        external_ctx_links=[{"snapshot_id": "snap-1"}],
        risk_flags=["delivery_risk"],
        evidence_status=SimpleNamespace(value="supported"),
        review_status="approved",
    )
    requirement = SimpleNamespace(
        requirement_text="Support 100k users",
        category="technical",
        mandatory=True,
        source_location={"page_no": 3},
    )

    content = _matrix_csv([(item, requirement)])
    rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))

    assert rows[0][0:6] == [
        "requirement",
        "category",
        "mandatory",
        "source_location",
        "ai_draft",
        "current_answer",
    ]
    assert rows[1][0] == "Support 100k users"
    assert "AI draft" in rows[1]
    assert "Human answer" in rows[1]
    assert "cap-1" in rows[1]


def test_batch_review_and_export_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/v1/response-matrices/{matrix_id}/batch-review"]
    assert "get" in paths["/api/v1/response-matrices/{matrix_id}/export"]


@pytest.mark.asyncio
async def test_response_item_snapshot_preserves_pre_change_audit_state() -> None:
    session = _SnapshotSession()
    user_id = uuid.uuid4()
    service = TenderService(session, uuid.uuid4(), user_id)
    item = SimpleNamespace(
        id=uuid.uuid4(),
        version=3,
        response_text="当前回答",
        ai_draft="不可变 AI 草稿",
        current_answer="当前回答",
        evidence_status=SimpleNamespace(value="supported"),
        evidence_refs=[{"asset_id": "evidence-1"}],
        risks=["交付待确认"],
        internal_exp_links=[{"asset_id": "experience-1"}],
        internal_cap_links=[{"asset_id": "capability-1"}],
        external_ctx_links=[{"snapshot_id": "snapshot-1"}],
        risk_flags=[],
        review_status="pending",
        reviewer_id=None,
        review_note=None,
        approved_at=None,
    )

    await service._snapshot_item(item, "edit_and_approve")

    assert len(session.added) == 1
    snapshot = session.added[0]
    assert snapshot.response_item_id == item.id
    assert snapshot.version == 3
    assert snapshot.changed_by_id == user_id
    assert snapshot.change_type == "edit_and_approve"
    assert snapshot.item_snapshot["ai_draft"] == "不可变 AI 草稿"
    assert snapshot.item_snapshot["current_answer"] == "当前回答"
    assert snapshot.item_snapshot["internal_exp_links"] == [{"asset_id": "experience-1"}]


@pytest.mark.asyncio
async def test_matrix_history_list_returns_summary_counts_with_governance_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    tender_id = uuid.uuid4()
    first = SimpleNamespace(id=uuid.uuid4())
    second = SimpleNamespace(id=uuid.uuid4())
    session = _MatrixListSession([(first, 2), (second, 0)], total=2)
    service = TenderService(session, workspace_id, uuid.uuid4())

    async def get_tender(received_tender_id):
        assert received_tender_id == tender_id
        return SimpleNamespace(id=tender_id)

    monkeypatch.setattr(service, "get_tender", get_tender)
    rows, total = await service.list_matrices(tender_id, page=2, page_size=10)

    assert rows == [(first, 2), (second, 0)]
    assert total == 2
    assert len(session.executed) == 1
    compiled = str(session.executed[0])
    assert "response_matrices.workspace_id" in compiled
    assert "response_matrices.is_deleted IS false" in compiled
    assert "response_matrix_items.workspace_id" in compiled
    assert "response_matrix_items.is_deleted IS false" in compiled
    assert "LIMIT" in compiled
    assert "OFFSET" in compiled
    assert session.scalar_statement is not None


@pytest.mark.asyncio
async def test_matrix_item_filter_keeps_requirement_and_item_workspace_bound() -> None:
    workspace_id = uuid.uuid4()
    matrix_id = uuid.uuid4()
    item = SimpleNamespace(id=uuid.uuid4())
    requirement = SimpleNamespace(id=uuid.uuid4())
    session = _FilteredMatrixSession([(item, requirement)])
    service = TenderService(session, workspace_id, uuid.uuid4())

    matrix, rows = await service.list_matrix_items(
        matrix_id,
        category="technical",
        evidence_status="missing_evidence",
        review_status="needs_evidence",
        risk_flag="missing_evidence",
    )

    assert rows == [(item, requirement)]
    assert matrix.id
    compiled = str(session.statement)
    assert "response_matrix_items.workspace_id" in compiled
    assert "response_matrix_items.is_deleted IS false" in compiled
    assert "tender_requirements.workspace_id" in compiled
    assert "tender_requirements.is_deleted IS false" in compiled
    assert "tender_requirements.category" in compiled
    assert "response_matrix_items.evidence_status" in compiled
    assert "response_matrix_items.review_status" in compiled
