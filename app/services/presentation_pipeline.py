import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import (
    HtmlArtifact,
    Presentation,
    PresentationStatus,
    StyleProfileStatus,
    VisualStyleProfile,
)
from app.presentation.layouts.diagnostics import diagnose_layout
from app.presentation.layouts.engine import LayoutEngine
from app.schemas.presentation import PresentationSpecData, VisualStyleProfileData
from app.services.evidence_guard import EvidenceGuard
from app.services.fact_ledger_service import FactLedgerService
from app.services.html_renderer import HTMLRenderer


async def run_presentation_generation(
    presentation_id: uuid.UUID,
    run_id: uuid.UUID,
    style_profile_id: uuid.UUID,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        presentation = await session.scalar(
            select(Presentation).where(
                Presentation.id == presentation_id,
                Presentation.solution_run_id == run_id,
                Presentation.style_profile_id == style_profile_id,
                Presentation.is_deleted.is_(False),
            )
        )
        if presentation is None:
            return
        try:
            presentation.status = PresentationStatus.PLANNING
            await session.commit()

            style_profile = await session.scalar(
                select(VisualStyleProfile).where(
                    VisualStyleProfile.id == style_profile_id,
                    VisualStyleProfile.workspace_id == presentation.workspace_id,
                    VisualStyleProfile.status == StyleProfileStatus.CONFIRMED,
                    VisualStyleProfile.is_deleted.is_(False),
                )
            )
            if style_profile is None:
                raise ValueError("confirmed style profile is missing")
            style = VisualStyleProfileData.model_validate(style_profile.visual_json)
            ledger = await FactLedgerService(session, presentation.workspace_id).build_ledger(
                run_id
            )
            spec = _mock_plan(presentation_id, ledger)

            presentation.status = PresentationStatus.VALIDATING
            await session.commit()
            guard = EvidenceGuard()
            binding_report, guard_snapshot = guard.validate_bindings(spec, ledger)
            if not binding_report.passed:
                raise ValueError(
                    "evidence guard rejected presentation bindings: "
                    + ",".join(failure.code for failure in binding_report.failures)
                )
            positioned_spec = LayoutEngine().position(spec)
            layout_report = diagnose_layout(positioned_spec)
            if not layout_report.passed:
                raise ValueError("layout validation failed: " + ",".join(layout_report.errors))
            final_report = guard.validate_positioned_spec(positioned_spec, ledger, guard_snapshot)
            if not final_report.passed:
                raise ValueError(
                    "evidence guard rejected positioned presentation: "
                    + ",".join(failure.code for failure in final_report.failures)
                )

            presentation.status = PresentationStatus.RENDERING
            await session.commit()
            html = HTMLRenderer().render(positioned_spec, style)
            presentation.spec = positioned_spec.model_dump(mode="json")
            presentation.status = PresentationStatus.READY
            presentation.completed_at = datetime.now(UTC)
            presentation.error_code = None
            session.add(
                HtmlArtifact(
                    workspace_id=presentation.workspace_id,
                    presentation_id=presentation.id,
                    version=presentation.version,
                    html=html,
                    css="embedded",
                    assets=[],
                    render_report={
                        "renderer": "jinja2-deterministic-v1",
                        "schema_version": positioned_spec.schema_version,
                        "validated_slides": len(positioned_spec.slides),
                        "prechecked_components": binding_report.checked_components,
                        "final_checked_components": final_report.checked_components,
                        "layout_diagnostics": list(layout_report.errors),
                    },
                    provider_mode="deterministic",
                    status="ready",
                    upstream_status="released",
                    artifact_paths={},
                    preview_screenshot_keys=[],
                )
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            presentation = await session.scalar(
                select(Presentation).where(Presentation.id == presentation_id)
            )
            if presentation is None:
                return
            presentation.status = PresentationStatus.FAILED
            presentation.error_code = _error_code(exc)
            presentation.completed_at = datetime.now(UTC)
            await session.commit()


def _mock_plan(presentation_id, ledger) -> PresentationSpecData:
    if not ledger.facts:
        raise ValueError("fact ledger contains no released facts")
    fact = ledger.facts[0]
    evidence_ids = [item.evidence_id for item in fact.evidence]
    source_ids = list(dict.fromkeys(item.source_id for item in fact.evidence))
    return PresentationSpecData.model_validate(
        {
            "schema_version": "slide-schema-v1",
            "presentation_id": presentation_id,
            "slides": [
                {
                    "slide_id": uuid.uuid4(),
                    "layout_token": "title_body",
                    "components": [
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "title",
                            "text": "企业数字化转型方案",
                        },
                        {
                            "component_id": uuid.uuid4(),
                            "component_type": "evidence_card",
                            "heading": fact.claim_key,
                            "body": fact.verbatim_text,
                            "fact_binding": {
                                "claim_id": fact.claim_id,
                                "claim_key": fact.claim_key,
                                "evidence_ids": evidence_ids,
                                "source_ids": source_ids,
                                "content_mode": "verbatim",
                            },
                        },
                    ],
                }
            ],
        }
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "PRESENTATION_SCHEMA_INVALID"
    if isinstance(exc, ValueError) and "evidence guard" in str(exc):
        return "PRESENTATION_EVIDENCE_REJECTED"
    return "PRESENTATION_GENERATION_FAILED"
