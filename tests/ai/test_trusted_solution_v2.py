import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.ai.pipelines.solution import generate_solution
from app.ai.pipelines.trust import generate_trusted_solution
from app.ai.schemas import (
    CapabilityDraft,
    CustomerProfileDraft,
    ExperienceDraft,
    RetrievalSnapshot,
    RetrievedCapability,
    RetrievedExperience,
    SolutionContext,
    SourceSnapshot,
)


def make_context() -> SolutionContext:
    return SolutionContext(
        schema_version="solution-v2",
        trace_id="trace-v2-test",
        trust_deadline_seconds=5,
        trust_retry_budget=12,
        customer_profile=CustomerProfileDraft(
            customer_name="星瀚精工集团",
            constraints=["不允许自动停线"],
            profile_summary="客户希望做人工确认的单线试点。",
            source_ids=["SRC-CUST-001"],
        ),
        current_requirement="使用 A 方案，负责人待核实，两周交付可行性待确认。",
    )


def source_snapshot(source_id: str, *, valid: bool = True) -> SourceSnapshot:
    return SourceSnapshot(
        source_version="v2" if valid else "v3",
        reviewed_version="v2",
        permission_snapshot_id=f"perm-{source_id}",
        permission_valid=valid,
        available=True,
        invalid_reason=None if valid else "permission revoked after review",
        title=f"资料 {source_id}",
    )


def make_snapshot(*, valid_sources: bool = True) -> RetrievalSnapshot:
    return RetrievalSnapshot(
        experiences=[
            RetrievedExperience(
                asset_id="EXP-001",
                source_id="SRC-EXP-001",
                rank=1,
                match_reasons=["A 方案历史案例"],
                source_snapshot=source_snapshot("SRC-EXP-001", valid=valid_sources),
                data=ExperienceDraft(
                    name="A 方案历史试点",
                    problem="客户需要降低人工复看。",
                    solution="张三参与了 A 方案历史试点，异常由班组长确认。",
                    result="历史试点六周完成验收。",
                    source_id="SRC-EXP-001",
                    source_quote="张三参与了 A 方案历史试点，异常由班组长确认。",
                    source_anchor="paragraph-8",
                ),
            )
        ],
        capabilities=[
            RetrievedCapability(
                asset_id="CAP-001",
                source_id="SRC-CAP-001",
                rank=1,
                match_reasons=["支持人工确认"],
                source_snapshot=source_snapshot("SRC-CAP-001", valid=valid_sources),
                data=CapabilityDraft(
                    name="异常人工确认",
                    description="系统将异常通知给指定责任人，由人工确认处理。",
                    limitations="不自动停线，不承诺固定交付周期。",
                    source_id="SRC-CAP-001",
                    source_quote="系统将异常通知给指定责任人，由人工确认处理。",
                    source_anchor="paragraph-3",
                ),
            ),
            RetrievedCapability(
                asset_id="CAP-002",
                source_id="SRC-CAP-002",
                rank=2,
                match_reasons=["补充人工责任边界"],
                source_snapshot=source_snapshot("SRC-CAP-002", valid=valid_sources),
                data=CapabilityDraft(
                    name="人工责任边界",
                    description="异常处理结论必须由获授权人员确认。",
                    limitations="AI 不替代最终责任人。",
                    source_id="SRC-CAP-002",
                    source_quote="异常处理结论必须由获授权人员确认。",
                    source_anchor="paragraph-5",
                ),
            ),
        ],
        created_at=datetime.now(UTC),
    )


def solution_result(owner_text: str = "A 方案的负责人是张三。") -> dict:
    return {
        "requirement_understanding": [
            {
                "text": "客户希望采用 A 方案。",
                "boundary": "ai_inference",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "initial_recommendations": [],
        "historical_evidence": [
            {
                "text": owner_text,
                "boundary": "historical_fact",
                "asset_id": "EXP-001",
                "source_id": "SRC-EXP-001",
            }
        ],
        "capability_composition": [
            {
                "text": "系统可通知责任人并由人工确认。",
                "boundary": "enterprise_capability",
                "asset_id": "CAP-001",
                "source_id": "SRC-CAP-001",
            }
        ],
        "prerequisites_and_risks": [],
        "pending_confirmations": [
            {
                "text": "当前项目负责人和两周交付可行性待确认。",
                "boundary": "pending_confirmation",
                "asset_id": None,
                "source_id": None,
            }
        ],
        "suggested_questions": ["谁负责当前项目？"],
    }


class StaticClient:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.call_count = 0

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.call_count += 1
        return self.result


class ClaimClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        items = json.loads(
            user_prompt.split("--- 报告项开始 ---", 1)[1].split("--- 报告项结束 ---", 1)[0]
        )
        claims = []
        for item in items:
            text = item["text"]
            if "负责人" in text:
                claim_type = "owner_assignment"
                risk_level = "high"
            elif item["boundary"] == "enterprise_capability":
                claim_type = "enterprise_capability"
                risk_level = "high"
            elif item["boundary"] == "pending_confirmation":
                claim_type = "pending_confirmation"
                risk_level = "medium"
            else:
                claim_type = "recommendation"
                risk_level = "medium"
            claims.append(
                {
                    "item_ref": item["item_ref"],
                    "text": text,
                    "claim_type": claim_type,
                    "risk_level": risk_level,
                }
            )
        return {"claims": claims}


class SemanticVerifier:
    def __init__(self, *, contradict_owner: bool = False) -> None:
        self.contradict_owner = contradict_owner
        self.seen_multi_evidence = False

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        bundle = json.loads(
            user_prompt.split("--- 核验包开始 ---", 1)[1].split("--- 核验包结束 ---", 1)[0]
        )
        reviews = []
        for claim in bundle["claims"]:
            text = claim["text"]
            refs = claim["candidate_evidence_refs"]
            if "负责人是张三" in text:
                status = "contradicted" if self.contradict_owner else "insufficient"
                reason = "资料只写张三参与项目，不能升级为负责人。"
                refs = []
            elif claim["boundary"] in {"historical_fact", "enterprise_capability"}:
                status = "entailed"
                reason = "证据直接支持。"
                if "人工确认" in text:
                    refs = [
                        item["evidence_id"]
                        for item in bundle["evidence_candidates"]
                        if item["asset_id"].startswith("CAP-")
                    ]
                    self.seen_multi_evidence = len(refs) >= 2
            else:
                status = "entailed"
                reason = "边界表达保守。"
                refs = []
            reviews.append(
                {
                    "claim_id": claim["claim_id"],
                    "status": status,
                    "reason": reason,
                    "evidence_refs": refs,
                    "uncertainty_score": 0.1 if status == "entailed" else 0.7,
                }
            )
        return {"reviews": reviews}


class RevisionClient:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        assert "failed_item_refs" in user_prompt
        return solution_result("张三参与过 A 方案历史试点。")


async def initial_solution(snapshot: RetrievalSnapshot):
    return await generate_solution(
        make_context(),
        snapshot,
        StaticClient(solution_result()),
    )


def test_solution_v2_downgrades_owner_claim_and_keeps_only_used_sources() -> None:
    snapshot = make_snapshot()
    verifier = SemanticVerifier()
    trusted = asyncio.run(
        generate_trusted_solution(
            make_context(),
            snapshot,
            asyncio.run(initial_solution(snapshot)),
            ClaimClient(),
            verifier,
            RevisionClient(),
            max_revisions=1,
        )
    )

    assert trusted.schema_version == "solution-v2"
    assert trusted.recommended_action == "release"
    assert trusted.quality_attempts[0].recommended_action == "review"
    assert trusted.historical_evidence[0].text == "张三参与过 A 方案历史试点。"
    assert trusted.verification_summary.contradicted == 0
    assert trusted.verification_summary.insufficient == 0
    assert verifier.seen_multi_evidence is True
    assert {(source.asset_id, source.source_id) for source in trusted.sources} == {
        ("EXP-001", "SRC-EXP-001"),
        ("CAP-001", "SRC-CAP-001"),
        ("CAP-002", "SRC-CAP-002"),
    }


def test_solution_v2_marks_stale_or_revoked_evidence_invalid_and_blocks() -> None:
    snapshot = make_snapshot(valid_sources=False)
    trusted = asyncio.run(
        generate_trusted_solution(
            make_context(),
            snapshot,
            asyncio.run(initial_solution(snapshot)),
            ClaimClient(),
            SemanticVerifier(),
            RevisionClient(),
            max_revisions=0,
        )
    )

    assert trusted.recommended_action == "block"
    assert trusted.verification_summary.invalid >= 1
    assert trusted.sources == []


def test_solution_v2_rejects_verifier_evidence_outside_bundle() -> None:
    class BadVerifier(SemanticVerifier):
        async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            result = await super().generate_json(system_prompt, user_prompt)
            result["reviews"][0]["evidence_refs"] = ["ev_forged"]
            return result

    snapshot = make_snapshot()
    with pytest.raises(ValueError, match="outside the candidate bundle"):
        asyncio.run(
            generate_trusted_solution(
                make_context(),
                snapshot,
                asyncio.run(initial_solution(snapshot)),
                ClaimClient(),
                BadVerifier(),
                RevisionClient(),
                max_revisions=0,
            )
        )


def test_solution_v2_trace_ids_do_not_share_metadata() -> None:
    from app.ai.runtime import AIRunMetadata, AIRunStatus, InMemoryRunRecorder

    recorder = InMemoryRunRecorder()
    for trace_id in ("trace-a", "trace-b"):
        recorder.record(
            AIRunMetadata(
                trace_id=trace_id,
                method="generate_solution",
                stage="trust",
                status=AIRunStatus.SUCCEEDED,
                model_version="stub",
                prompt_version="trust-v2",
                schema_version="solution-v2",
                embedding_version="embedding-v1",
                parameter_version="test=true",
                started_at=datetime.now(UTC),
                duration_ms=1,
            )
        )

    assert recorder.get("trace-a").trace_id == "trace-a"
    assert recorder.get("trace-b").trace_id == "trace-b"
    assert recorder.get("missing") is None


def test_solution_v2_can_generate_initial_solution_inside_one_budget() -> None:
    snapshot = make_snapshot()
    trusted = asyncio.run(
        generate_trusted_solution(
            make_context(),
            snapshot,
            None,
            ClaimClient(),
            SemanticVerifier(),
            RevisionClient(),
            generation_client=StaticClient(solution_result("张三参与过 A 方案历史试点。")),
            max_revisions=0,
        )
    )

    assert trusted.schema_version == "solution-v2"
    assert trusted.trace_id == "trace-v2-test"


def test_trust_budget_counts_attempts_without_deadlock() -> None:
    from app.ai.runtime import TrustRunBudget

    budget = TrustRunBudget(deadline_seconds=1, max_attempts=2)
    budget.consume_attempt()
    budget.consume_attempt()

    assert budget.attempts_used == 2
    with pytest.raises(RuntimeError, match="retry budget exhausted"):
        budget.consume_attempt()


def test_high_risk_owner_label_cannot_be_downgraded_by_claim_model() -> None:
    class MislabelingClaimClient(ClaimClient):
        async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            result = await super().generate_json(system_prompt, user_prompt)
            for claim in result["claims"]:
                if "负责人" in claim["text"]:
                    claim["claim_type"] = "recommendation"
                    claim["risk_level"] = "low"
            return result

    snapshot = make_snapshot()
    trusted = asyncio.run(
        generate_trusted_solution(
            make_context(),
            snapshot,
            asyncio.run(initial_solution(snapshot)),
            MislabelingClaimClient(),
            SemanticVerifier(),
            RevisionClient(),
            max_revisions=0,
        )
    )

    owner_claim = next(claim for claim in trusted.claims if "负责人" in claim.text)
    assert owner_claim.claim_type == "owner_assignment"
    assert owner_claim.risk_level == "high"
