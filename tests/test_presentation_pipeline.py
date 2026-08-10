import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.db.models import HtmlArtifact, PresentationStatus, StyleProfileStatus
from app.schemas.presentation import FactAtom, FactLedger, LedgerEvidence
from app.services import presentation_pipeline as module


@pytest.mark.asyncio
async def test_pipeline_builds_validated_deterministic_html_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    style_profile_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    presentation = SimpleNamespace(
        id=presentation_id,
        workspace_id=workspace_id,
        solution_run_id=run_id,
        style_profile_id=style_profile_id,
        version=1,
        status=PresentationStatus.QUEUED,
        spec=None,
        completed_at=None,
        error_code=None,
        audience="集团管理层",
        language="zh-CN",
        mode="balanced",
    )
    style_profile = SimpleNamespace(
        id=style_profile_id,
        status=StyleProfileStatus.CONFIRMED,
        visual_json={
            "palette": {
                "primary": "#123456",
                "secondary": "#345678",
                "accent": "#E11D48",
                "background": "#FFFFFF",
                "foreground": "#111827",
            },
            "typography": {
                "heading_font": "Microsoft YaHei",
                "body_font": "Inter",
                "base_size_px": 18,
                "scale_ratio": 1.5,
            },
            "spacing_grid": {
                "base_unit_px": 8,
                "slide_padding_units": 8,
                "component_gap_units": 3,
            },
            "layout_grammar": ["title-and-evidence"],
        },
    )
    ledger = FactLedger(
        run_id=run_id,
        facts=(
            FactAtom(
                claim_id=uuid.uuid4(),
                claim_key="financial:revenue",
                verbatim_text="2025年营业收入为1438亿元",
                boundary="verified_fact",
                evidence=(
                    LedgerEvidence(
                        evidence_id=uuid.uuid4(),
                        source_id=uuid.uuid4(),
                        source_version=1,
                        quote="2025年营收1438亿元",
                    ),
                ),
            ),
        ),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[presentation, style_profile]),
        add=Mock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    observed_statuses: list[PresentationStatus] = []

    async def capture_commit() -> None:
        observed_statuses.append(presentation.status)

    session.commit.side_effect = capture_commit

    @asynccontextmanager
    async def session_context():
        yield session

    monkeypatch.setattr(module, "get_session_factory", lambda: session_context)
    ledger_service = SimpleNamespace(build_ledger=AsyncMock(return_value=ledger))
    monkeypatch.setattr(module, "FactLedgerService", lambda *_: ledger_service)

    await module.run_presentation_generation(presentation_id, run_id, style_profile_id)

    assert observed_statuses == [
        PresentationStatus.PLANNING,
        PresentationStatus.VALIDATING,
        PresentationStatus.RENDERING,
        PresentationStatus.READY,
    ]
    assert presentation.spec["schema_version"] == "positioned-spec-v1"
    artifact = session.add.call_args.args[0]
    assert isinstance(artifact, HtmlArtifact)
    assert artifact.status == "ready"
    assert "--primary-color: #123456" in artifact.html
    assert "2025年营业收入为1438亿元" in artifact.html
    assert artifact.render_report["validated_slides"] == 4
    assert artifact.render_report["prechecked_components"] == 3
    assert artifact.render_report["final_checked_components"] == 3
