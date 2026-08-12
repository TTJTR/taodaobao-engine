import hashlib
from types import SimpleNamespace

import pytest

from app.core.errors import AppError, ErrorCode
from app.services.tender_service import (
    TenderService,
    _evidence_link,
    _validate_document_location,
)


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
