import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.integrations.presentation_planner import AIEngineSlidePlanner, MockSlidePlanner
from app.schemas.presentation import (
    FactAtom,
    FactLedger,
    LedgerEvidence,
    PlanningFact,
    SlidePlanData,
    SlidePlanningContext,
    SlidePlanningStyleConstraints,
)
from app.services.ai_harness import (
    PresentationPlanningHarness,
    PresentationPlanningUnavailable,
)
from app.services.evidence_guard import EvidenceGuard
from app.services.slide_plan_materializer import SlidePlanMaterializer


def _ledger() -> FactLedger:
    facts = []
    for index, text in enumerate(("2025年营业收入为1438亿元", "具备全国交付能力"), start=1):
        facts.append(
            FactAtom(
                claim_id=uuid.uuid4(),
                claim_key=f"verified:{index}",
                verbatim_text=text,
                boundary="verified_fact",
                evidence=(
                    LedgerEvidence(
                        evidence_id=uuid.uuid4(),
                        source_id=uuid.uuid4(),
                        source_version=index,
                        quote=text,
                    ),
                ),
            )
        )
    return FactLedger(run_id=uuid.uuid4(), facts=tuple(facts))


def _context() -> SlidePlanningContext:
    return SlidePlanningContext(
        presentation_id=uuid.uuid4(),
        audience="集团管理层",
        language="zh-CN",
        mode="balanced",
    )


def _catalog(ledger: FactLedger) -> tuple[PlanningFact, ...]:
    return tuple(
        PlanningFact(
            claim_id=fact.claim_id,
            claim_key=fact.claim_key,
            boundary=fact.boundary,
            verbatim_text=fact.verbatim_text,
            source_count=len(fact.evidence),
        )
        for fact in ledger.facts
    )


def _constraints() -> SlidePlanningStyleConstraints:
    return SlidePlanningStyleConstraints(
        allowed_layout_tokens=(
            "cover",
            "evidence_grid",
            "metric_highlight",
            "process",
            "source_list",
        ),
        max_pages=12,
        max_components_per_page=4,
    )


def test_slide_plan_schema_rejects_ai_authored_html_or_text_fields() -> None:
    claim_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        SlidePlanData.model_validate(
            {
                "schema_version": "slide-plan-v1",
                "presentation_id": uuid.uuid4(),
                "pages": [
                    {
                        "slide_id": uuid.uuid4(),
                        "purpose": "evidence",
                        "layout_token": "title_body",
                        "character_budget": 500,
                        "allow_pagination": True,
                        "components": [
                            {
                                "component_id": uuid.uuid4(),
                                "component_type": "evidence_card",
                                "claim_ids": [claim_id],
                                "priority": 1,
                                "html": "<script>invent()</script>",
                            }
                        ],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_planning_harness_retries_unknown_claim_then_accepts_valid_plan() -> None:
    ledger = _ledger()
    context = _context()
    catalog = _catalog(ledger)
    constraints = _constraints()
    valid = await MockSlidePlanner().plan_slides(context, catalog, constraints)
    invalid = valid.model_copy(deep=True)
    component = invalid.pages[1].components[0]
    invalid_component = component.model_copy(update={"claim_ids": [uuid.uuid4()]})
    invalid_page = invalid.pages[1].model_copy(update={"components": [invalid_component]})
    invalid = invalid.model_copy(update={"pages": [invalid.pages[0], invalid_page]})
    planner = AsyncMock()
    planner.plan_slides.side_effect = [invalid, valid]

    result = await PresentationPlanningHarness(
        planner, max_retries=1, retry_delay_seconds=0
    ).run_slide_planning(context, catalog, constraints)

    assert result == valid
    assert planner.plan_slides.await_count == 2


@pytest.mark.asyncio
async def test_planning_harness_exhaustion_never_falls_back_to_mock() -> None:
    ledger = _ledger()
    planner = AsyncMock()
    planner.plan_slides.side_effect = TimeoutError("model timeout")

    with pytest.raises(PresentationPlanningUnavailable):
        await PresentationPlanningHarness(
            planner, max_retries=2, retry_delay_seconds=0
        ).run_slide_planning(_context(), _catalog(ledger), _constraints())

    assert planner.plan_slides.await_count == 3


@pytest.mark.asyncio
async def test_materializer_uses_only_ledger_verbatim_text_and_passes_guard() -> None:
    ledger = _ledger()
    context = _context()
    plan = await MockSlidePlanner().plan_slides(context, _catalog(ledger), _constraints())

    spec = SlidePlanMaterializer().materialize(plan, ledger)
    report, _ = EvidenceGuard().validate_bindings(spec, ledger)
    rendered_text = spec.model_dump_json()

    assert report.passed is True
    assert all(fact.verbatim_text in rendered_text for fact in ledger.facts)
    assert "html" not in plan.model_dump_json().lower()
    assert "css" not in plan.model_dump_json().lower()


@pytest.mark.asyncio
async def test_live_planner_fails_clearly_when_molly_contract_is_missing() -> None:
    ledger = _ledger()

    with pytest.raises(RuntimeError, match="does not implement"):
        await AIEngineSlidePlanner(object()).plan_slides(
            _context(), _catalog(ledger), _constraints()
        )
