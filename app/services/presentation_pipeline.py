import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select

from app.contracts.presentation import SlidePlanner
from app.db.database import get_session_factory
from app.db.models import (
    HtmlArtifact,
    Presentation,
    PresentationStatus,
    StyleProfileStatus,
    VisualStyleProfile,
)
from app.db.repositories import PresentationRenderSnapshotRepository
from app.presentation.layouts.diagnostics import diagnose_layout
from app.presentation.layouts.engine import LayoutEngine
from app.presentation.layouts.paginator import SlidePaginator
from app.presentation.layouts.registry import default_layout_registry
from app.presentation.render_ir import RenderIRBuilder, SystemLabelCatalog
from app.presentation.style.legacy_adapter import adapt_legacy_style_profile
from app.schemas.presentation import (
    PlanningFact,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
    VisualStyleProfileData,
)
from app.schemas.style_profile import VisualStyleProfileV2
from app.services.ai_harness import PresentationPlanningHarness, PresentationPlanningUnavailable
from app.services.evidence_guard import EvidenceGuard
from app.services.fact_ledger_service import FactLedgerService
from app.services.html_renderer import HTMLRenderer
from app.services.html_renderer_v2 import HTMLRendererV2, HTMLShadowAuditor
from app.services.slide_plan_materializer import SlidePlanMaterializer


async def run_presentation_generation(
    presentation_id: uuid.UUID,
    run_id: uuid.UUID,
    style_profile_id: uuid.UUID,
    planner: SlidePlanner | None = None,
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
            planning_context = SlidePlanningContext(
                presentation_id=presentation.id,
                audience=presentation.audience,
                language=presentation.language,
                mode=presentation.mode,
            )
            fact_catalog = tuple(
                PlanningFact(
                    claim_id=fact.claim_id,
                    claim_key=fact.claim_key,
                    boundary=fact.boundary,
                    verbatim_text=fact.verbatim_text,
                    source_count=len({item.source_id for item in fact.evidence}),
                )
                for fact in ledger.facts
            )
            recognized_layouts = tuple(
                token for token in style.layout_grammar if token in default_layout_registry.tokens
            )
            style_constraints = SlidePlanningStyleConstraints(
                allowed_layout_tokens=default_layout_registry.tokens,
                preferred_layout_tokens=recognized_layouts,
                max_pages=12,
                max_components_per_page=4,
            )
            if planner is None:
                raise PresentationPlanningUnavailable("slide planner is not configured")
            plan = await PresentationPlanningHarness(planner).run_slide_planning(
                planning_context, fact_catalog, style_constraints
            )
            spec = SlidePaginator().paginate(SlidePlanMaterializer().materialize(plan, ledger))
            text_provenance = SystemLabelCatalog().build_provenance(spec, ledger)

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

            render_style = _render_style_v2(style_profile.visual_json, style_profile.id)
            render_ir = RenderIRBuilder().build(
                positioned_spec,
                render_style,
                compiled_style_hash=_compiled_style_hash(
                    style_profile.visual_json, render_style
                ),
                text_provenance=text_provenance,
            )
            render_ir_report = guard.validate_render_ir(render_ir, ledger)
            if not render_ir_report.passed:
                raise ValueError(
                    "evidence guard rejected RenderIR: "
                    + ",".join(failure.code for failure in render_ir_report.failures)
                )
            render_ir_hash = _canonical_hash(render_ir.model_dump(mode="json"))
            fact_ledger_json = ledger.model_dump(mode="json")
            await PresentationRenderSnapshotRepository(
                session, presentation.workspace_id
            ).create_or_verify(
                presentation_id=presentation.id,
                version=presentation.version,
                schema_version="presentation-render-snapshot-v1",
                fact_ledger_hash=_canonical_hash(fact_ledger_json),
                positioned_spec_hash=render_ir.positioned_spec_hash,
                compiled_style_hash=render_ir.compiled_style_hash,
                render_ir_hash=render_ir_hash,
                fact_ledger_json=fact_ledger_json,
                render_ir_json=render_ir.model_dump(mode="json"),
                renderer_versions={
                    "render_ir_builder": "render-ir-builder-v1",
                    "text_layout": "text-layout-v1",
                    "system_labels": SystemLabelCatalog.version,
                    "html_renderer": "jinja2-deterministic-v1",
                    "html_renderer_v2": HTMLRendererV2.version,
                },
                diagnostics={
                    "mode": "shadow",
                    "layout_errors": list(layout_report.errors),
                    "prechecked_components": binding_report.checked_components,
                    "positioned_checked_components": final_report.checked_components,
                    "render_ir_checked_text_runs": render_ir_report.checked_components,
                },
            )

            presentation.status = PresentationStatus.RENDERING
            await session.commit()
            html = HTMLRenderer().render(positioned_spec, style)
            shadow_html = HTMLRendererV2().render(render_ir)
            shadow_audit = HTMLShadowAuditor().audit(html, shadow_html, render_ir)
            if not shadow_audit.passed:
                raise ValueError(
                    "HTML v2 shadow audit failed: " + ",".join(shadow_audit.errors)
                )
            shadow_html_hash = hashlib.sha256(shadow_html.encode()).hexdigest()
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
                        "render_ir_schema_version": render_ir.schema_version,
                        "render_ir_hash": render_ir_hash,
                        "render_ir_checked_text_runs": render_ir_report.checked_components,
                        "render_ir_mode": "shadow",
                        "html_v2_renderer": HTMLRendererV2.version,
                        "html_v2_hash": shadow_html_hash,
                        "html_v2_shadow_audit": {
                            "passed": shadow_audit.passed,
                            "slide_count": shadow_audit.slide_count,
                            "text_node_count": shadow_audit.text_node_count,
                            "errors": list(shadow_audit.errors),
                        },
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


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "PRESENTATION_SCHEMA_INVALID"
    if isinstance(exc, ValueError) and "evidence guard" in str(exc):
        return "PRESENTATION_EVIDENCE_REJECTED"
    if isinstance(exc, PresentationPlanningUnavailable):
        return "PRESENTATION_PLANNER_UNAVAILABLE"
    return "PRESENTATION_GENERATION_FAILED"


def _render_style_v2(visual_json: dict, profile_id: uuid.UUID) -> VisualStyleProfileV2:
    if visual_json.get("schema_version") == "style-profile-v2":
        return VisualStyleProfileV2.model_validate(visual_json)
    return adapt_legacy_style_profile(visual_json, profile_id=profile_id)


def _compiled_style_hash(visual_json: dict, style: VisualStyleProfileV2) -> str:
    confirmed = visual_json.get("confirmed_template") or {}
    value = confirmed.get("compiled_template_hash")
    if isinstance(value, str) and len(value) == 64:
        try:
            int(value, 16)
        except ValueError:
            pass
        else:
            return value.lower()
    return _canonical_hash(style.model_dump(mode="json"))


def _canonical_hash(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
