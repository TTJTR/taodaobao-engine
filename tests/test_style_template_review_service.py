import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from test_pptx_parser import _pptx

from app.core.errors import AppError
from app.db.models import (
    StyleProfile,
    StyleProfileStatus,
    StyleTemplateStatus,
    StyleTemplateVersion,
)
from app.presentation.style.profile_builder import StyleProfileBuilder
from app.schemas.presentations import (
    ConfirmTemplateCandidateRequest,
    RegenerateTemplateCandidatesRequest,
)
from app.services.pptx_parser import SafePPTXParser
from app.services.presentation_service import PresentationService
from app.services.style_template_review_service import StyleTemplateReviewService


def _profile(workspace_id: uuid.UUID, user_id: uuid.UUID) -> StyleProfile:
    profile_id = uuid.uuid4()
    features = SafePPTXParser().extract_features(_pptx(), mode="sanitized_visual")
    style = StyleProfileBuilder().build(features, profile_id=profile_id)
    profile = StyleProfile(
        workspace_id=workspace_id,
        created_by_id=user_id,
        name="V1.1 风格画像",
        version=3,
        reference_versions=[{"file_hash": "a" * 64}],
        status=StyleProfileStatus.DRAFT,
        visual_json=style.model_dump(mode="json"),
        narrative_json={},
        conflict_notes=[],
    )
    profile.id = profile_id
    return profile


@pytest.mark.asyncio
async def test_regenerate_persists_sanitized_candidates_pending_review() -> None:
    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(workspace_id, user_id)
    session = AsyncMock()
    session.add = Mock()
    session.scalar.return_value = 0
    service = StyleTemplateReviewService(session, workspace_id, user_id)
    service._profile = AsyncMock(return_value=profile)  # type: ignore[method-assign]
    service._supersede_open_candidates = AsyncMock()  # type: ignore[method-assign]

    updated, candidates = await service.regenerate(
        profile.id,
        RegenerateTemplateCandidatesRequest(expected_profile_version=3),
    )

    assert updated.version == 4
    assert candidates
    assert all(item.status == StyleTemplateStatus.NEEDS_REVIEW for item in candidates)
    assert all(item.validation_report["passed"] for item in candidates)
    assert all("<script" not in item.preview_artifacts["html"].lower() for item in candidates)
    assert all("企业方案风格预览" in item.preview_artifacts["html"] for item in candidates)
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_uses_double_version_guard_and_rejects_peer_candidates() -> None:
    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(workspace_id, user_id)
    profile.version = 4
    selected = _candidate(profile, version=1, status=StyleTemplateStatus.NEEDS_REVIEW)
    peer = _candidate(profile, version=1, status=StyleTemplateStatus.NEEDS_REVIEW)
    previous = _candidate(profile, version=0, status=StyleTemplateStatus.CONFIRMED)
    session = AsyncMock()
    session.scalar.return_value = selected
    session.scalars.return_value = SimpleNamespace(all=lambda: [selected, peer, previous])
    service = StyleTemplateReviewService(session, workspace_id, user_id)
    service._profile = AsyncMock(return_value=profile)  # type: ignore[method-assign]

    updated, confirmed = await service.confirm(
        profile.id,
        selected.candidate_id,
        ConfirmTemplateCandidateRequest(
            expected_profile_version=4,
            expected_candidate_version=1,
        ),
    )

    assert confirmed.status == StyleTemplateStatus.CONFIRMED
    assert confirmed.confirmed_by_id == user_id
    assert peer.status == StyleTemplateStatus.REJECTED
    assert previous.status == StyleTemplateStatus.SUPERSEDED
    assert updated.version == 5
    assert updated.visual_json["confirmed_template"]["candidate_id"] == str(
        selected.candidate_id
    )


@pytest.mark.asyncio
async def test_regenerate_rejects_stale_profile_version() -> None:
    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(workspace_id, user_id)
    session = AsyncMock()
    service = StyleTemplateReviewService(session, workspace_id, user_id)
    service._profile = AsyncMock(return_value=profile)  # type: ignore[method-assign]

    with pytest.raises(AppError, match="版本已变化"):
        await service.regenerate(
            profile.id,
            RegenerateTemplateCandidatesRequest(expected_profile_version=2),
        )


@pytest.mark.asyncio
async def test_presentation_gate_requires_confirmed_candidate_after_generation() -> None:
    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(workspace_id, user_id)
    profile.visual_json = {**profile.visual_json, "template_generation": 1}

    with pytest.raises(AppError, match="必须先人工确认"):
        await PresentationService(AsyncMock(), workspace_id, user_id)._confirmed_template(profile)


@pytest.mark.asyncio
async def test_presentation_gate_resolves_hash_bound_confirmed_snapshot() -> None:
    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    profile = _profile(workspace_id, user_id)
    candidate = _candidate(profile, version=1, status=StyleTemplateStatus.CONFIRMED)
    profile.visual_json = {
        **profile.visual_json,
        "template_generation": 1,
        "confirmed_template": {
            "candidate_id": str(candidate.candidate_id),
            "version": candidate.version,
            "compiled_template_hash": candidate.compiled_template_hash,
        },
    }
    session = AsyncMock()
    session.scalar.return_value = candidate

    resolved = await PresentationService(
        session, workspace_id, user_id
    )._confirmed_template(profile)

    assert resolved is candidate


def _candidate(
    profile: StyleProfile,
    *,
    version: int,
    status: StyleTemplateStatus,
) -> StyleTemplateVersion:
    item = StyleTemplateVersion(
        workspace_id=profile.workspace_id,
        style_profile_id=profile.id,
        candidate_id=uuid.uuid4(),
        version=version,
        status=status,
        archetype_token="title_body",
        source_deck_hashes=[],
        feature_set_hash="a" * 64,
        compiled_template_hash="b" * 64,
        compiler_version="style-compiler-v1",
        compiled_template_json={},
        confidence_report={},
        validation_report={"passed": True},
        preview_artifacts={},
    )
    item.id = uuid.uuid4()
    return item
