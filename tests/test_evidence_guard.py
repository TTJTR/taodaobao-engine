import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from app.presentation.layouts.engine import LayoutEngine
from app.schemas.presentation import (
    FactAtom,
    FactLedger,
    LedgerEvidence,
    PositionedPresentationSpec,
    PresentationSpecData,
    SlideSchema,
)
from app.services.evidence_guard import EvidenceGuard
from app.services.fact_ledger_service import FactLedgerService


def _ledger() -> FactLedger:
    return FactLedger(
        run_id=uuid.uuid4(),
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
                        source_version=3,
                        quote="2025年营收1438亿元",
                    ),
                ),
            ),
        ),
    )


def _slide(ledger: FactLedger, text: str, *, claim_id: uuid.UUID | None = None) -> SlideSchema:
    fact = ledger.facts[0]
    evidence = fact.evidence[0]
    return SlideSchema.model_validate(
        {
            "slide_id": uuid.uuid4(),
            "layout_token": "title_body",
            "components": [
                {
                    "component_id": uuid.uuid4(),
                    "component_type": "key_message",
                    "text": text,
                    "fact_binding": {
                        "claim_id": claim_id or fact.claim_id,
                        "claim_key": fact.claim_key,
                        "evidence_ids": [evidence.evidence_id],
                        "source_ids": [evidence.source_id],
                        "content_mode": "verbatim",
                    },
                }
            ],
        }
    )


def test_evidence_guard_accepts_exact_released_fact_binding() -> None:
    ledger = _ledger()

    report = EvidenceGuard().validate(_slide(ledger, ledger.facts[0].verbatim_text), ledger)

    assert report.passed is True
    assert report.checked_components == 1
    assert report.failures == ()


def test_evidence_guard_rejects_modified_financial_number() -> None:
    ledger = _ledger()

    report = EvidenceGuard().validate(_slide(ledger, "2025年营业收入为1538亿元"), ledger)

    assert report.passed is False
    assert [failure.code for failure in report.failures] == ["VERBATIM_CONTENT_MISMATCH"]


def test_evidence_guard_rejects_fabricated_claim_id() -> None:
    ledger = _ledger()

    report = EvidenceGuard().validate(
        _slide(ledger, ledger.facts[0].verbatim_text, claim_id=uuid.uuid4()), ledger
    )

    assert report.passed is False
    assert [failure.code for failure in report.failures] == ["UNKNOWN_CLAIM"]


def test_final_evidence_guard_rejects_layout_stage_content_mutation() -> None:
    ledger = _ledger()
    semantic_spec = PresentationSpecData(
        schema_version="slide-schema-v1",
        presentation_id=uuid.uuid4(),
        slides=[_slide(ledger, ledger.facts[0].verbatim_text)],
    )
    guard = EvidenceGuard()
    preflight, snapshot = guard.validate_bindings(semantic_spec, ledger)
    positioned = LayoutEngine().position(semantic_spec)
    original_item = positioned.slides[0].components[0]
    mutated_component = original_item.component.model_copy(
        update={"text": "2025年营业收入为1538亿元"}
    )
    mutated_item = original_item.model_copy(update={"component": mutated_component})
    mutated_slide = positioned.slides[0].model_copy(update={"components": (mutated_item,)})
    mutated_spec = PositionedPresentationSpec(
        presentation_id=positioned.presentation_id,
        slides=(mutated_slide,),
    )

    report = guard.validate_positioned_spec(mutated_spec, ledger, snapshot)

    assert preflight.passed is True
    assert report.passed is False
    assert {failure.code for failure in report.failures} == {
        "VERBATIM_CONTENT_MISMATCH",
        "CONTENT_FINGERPRINT_MISMATCH",
    }


@pytest.mark.asyncio
async def test_fact_ledger_builds_only_joined_released_granted_facts() -> None:
    workspace_id = uuid.uuid4()
    run_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    source_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, result_version=2)
    claim = SimpleNamespace(
        id=claim_id,
        claim_key="capability:delivery",
        claim_text="具备全国交付能力",
        boundary="enterprise_capability",
    )
    evidence = SimpleNamespace(
        id=evidence_id,
        source_id=source_id,
        source_version=4,
        quote="全国交付服务覆盖",
    )
    result = Mock()
    result.all.return_value = [(claim, evidence)]
    session = AsyncMock()
    session.scalar.return_value = run
    session.execute.return_value = result

    ledger = await FactLedgerService(session, workspace_id).build_ledger(run_id)

    assert ledger.facts[0].claim_id == claim_id
    assert ledger.facts[0].evidence[0].source_id == source_id
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "claim_records.released IS true" in sql
    assert "evidence_records.permission_status" in sql
    assert "claim_records.is_deleted IS false" in sql
    assert "evidence_records.is_deleted IS false" in sql
