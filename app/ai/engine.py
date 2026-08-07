import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from app.ai.conversation import compact_context_payload
from app.ai.model_client import BailianChatClient, ModelClientError
from app.ai.pipelines.deep_research import generate_deep_research_stage
from app.ai.pipelines.extractor import (
    extract_capabilities_from_text,
    extract_experience_from_text,
    extract_profile_from_text,
    validate_raw_text,
)
from app.ai.pipelines.search_intent import extract_search_intent
from app.ai.pipelines.solution import finalize_solution_result, generate_solution
from app.ai.runtime import AIRunMetadata, AIRunStatus, InMemoryRunRecorder, RunTimer
from app.ai.schemas import (
    CandidateRecommendation,
    CapabilityDraft,
    CustomerProfileDraft,
    DeepResearchStageResult,
    ExperienceDraft,
    ProfileStatus,
    ResearchAudit,
    ResearchPlan,
    ResearchStage,
    ResearchSubquestion,
    ResearchTaskStatus,
    RetrievalSnapshot,
    SearchIntent,
    Solution,
    SolutionContext,
)

PROMPT_VERSIONS = {
    "extract_profile": "profile-v1",
    "extract_experience": "experience-v1",
    "extract_capabilities": "capability-v1",
    "extract_search_intent": "search-intent-v1",
    "generate_solution": "solution-v1",
}
SCHEMA_VERSION = "ai-schema-v1"
DEFAULT_EMBEDDING_VERSION = "text-embedding-v4-v1"

T = TypeVar("T")


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]: ...


class CapturingJsonClient:
    def __init__(self, client: JsonModelClient) -> None:
        self._client = client
        self.last_result: dict[str, Any] | None = None

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.last_result = await self._client.generate_json(system_prompt, user_prompt)
        return self.last_result


class StructureRepairJsonClient:
    """Ask the model once to repair its previous JSON without changing business facts."""

    def __init__(
        self,
        client: JsonModelClient,
        invalid_result: dict[str, Any],
        validation_error: Exception,
    ) -> None:
        self._client = client
        self._invalid_result = invalid_result
        self._validation_error = validation_error

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        repair_prompt = f"""{user_prompt}

上一次输出未通过结构校验。请只修复 JSON 结构，不增加新事实，并重新返回完整 JSON 对象。
校验错误：{str(self._validation_error)[:1200]}
上一次输出：{json.dumps(self._invalid_result, ensure_ascii=False)[:12000]}
"""
        return await self._client.generate_json(system_prompt, repair_prompt)


class BailianAIEngine:
    """The five frozen cross-service AI methods backed by Bailian."""

    def __init__(
        self,
        model_client: BailianChatClient,
        *,
        recorder: InMemoryRunRecorder | None = None,
        schema_version: str = SCHEMA_VERSION,
        embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    ) -> None:
        self._model_client = model_client
        self.recorder = recorder or InMemoryRunRecorder()
        self._schema_version = schema_version
        self._embedding_version = embedding_version

    @property
    def last_run(self) -> AIRunMetadata | None:
        return self.recorder.last_run

    async def _run(
        self,
        method: str,
        operation: Callable[[JsonModelClient], Awaitable[T]],
        *,
        stage: str | None = None,
        prompt_version: str | None = None,
        trace_id: str | None = None,
    ) -> T:
        timer = RunTimer(trace_id)
        repair_count = 0
        capture = CapturingJsonClient(self._model_client)
        try:
            try:
                result = await operation(capture)
            except (ValidationError, ValueError) as exc:
                if capture.last_result is None:
                    raise
                repair_count = 1
                repair_client = StructureRepairJsonClient(
                    self._model_client,
                    capture.last_result,
                    exc,
                )
                result = await operation(repair_client)
        except Exception as exc:
            self._record_run(
                timer,
                method,
                stage or method,
                prompt_version or PROMPT_VERSIONS[method],
                AIRunStatus.FAILED,
                repair_count,
                exc,
            )
            raise
        self._record_run(
            timer,
            method,
            stage or method,
            prompt_version or PROMPT_VERSIONS[method],
            AIRunStatus.SUCCEEDED,
            repair_count,
            None,
        )
        return result

    def _record_run(
        self,
        timer: RunTimer,
        method: str,
        stage: str,
        prompt_version: str,
        status: AIRunStatus,
        repair_count: int,
        error: Exception | None,
    ) -> None:
        model_attempts = getattr(self._model_client, "last_attempt_count", 1)
        retryable = isinstance(error, ModelClientError) and error.retryable
        error_code = error.code if isinstance(error, ModelClientError) else None
        if error is not None and error_code is None:
            error_code = "schema_validation_error"
        self.recorder.record(
            AIRunMetadata(
                trace_id=timer.trace_id,
                method=method,
                stage=stage,
                status=status,
                model_version=self._model_client.model_version,
                prompt_version=prompt_version,
                schema_version=self._schema_version,
                embedding_version=self._embedding_version,
                parameter_version=self._model_client.parameter_version,
                started_at=timer.started_at,
                duration_ms=timer.duration_ms,
                structure_repair_count=repair_count,
                model_attempt_count=model_attempts,
                error_code=error_code,
                retryable=retryable,
            )
        )

    async def extract_profile(
        self,
        raw_text: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        if not raw_text.strip():
            raise ValueError("raw_text must not be empty")
        if not any(source_id.strip() for source_id in source_ids):
            raise ValueError("source_ids must not be empty")
        result = await self._run(
            "extract_profile",
            lambda client: extract_profile_from_text(raw_text, source_ids, client),
        )
        return result.model_dump(mode="json")

    async def extract_experience(self, raw_text: str, source_id: str) -> dict[str, Any]:
        if not raw_text.strip():
            raise ValueError("raw_text must not be empty")
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        result = await self._run(
            "extract_experience",
            lambda client: extract_experience_from_text(raw_text, source_id, client),
        )
        return result.model_dump(mode="json")

    async def extract_capabilities(
        self,
        raw_text: str,
        source_id: str,
    ) -> list[dict[str, Any]]:
        if not raw_text.strip():
            raise ValueError("raw_text must not be empty")
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        result = await self._run(
            "extract_capabilities",
            lambda client: extract_capabilities_from_text(raw_text, source_id, client),
        )
        return [item.model_dump(mode="json") for item in result]

    async def extract_search_intent(self, context: dict[str, Any]) -> dict[str, Any]:
        validated_context = SolutionContext.model_validate(compact_context_payload(context))
        is_expert_routing = validated_context.task_type == "expert_routing"
        result = await self._run(
            "extract_search_intent",
            lambda client: extract_search_intent(validated_context, client),
            stage="expert_routing" if is_expert_routing else None,
            prompt_version="expert-routing-v1" if is_expert_routing else None,
            trace_id=validated_context.trace_id,
        )
        return result.model_dump(mode="json")

    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        validated_context = SolutionContext.model_validate(compact_context_payload(context))
        validated_snapshot = RetrievalSnapshot.model_validate(retrieval_snapshot)
        if validated_context.mode.value == "deep":
            if validated_context.trace_id is None:
                validated_context = validated_context.model_copy(update={"trace_id": str(uuid4())})
            result = await self._run(
                "generate_solution",
                lambda client: generate_deep_research_stage(
                    validated_context,
                    validated_snapshot,
                    client,
                ),
                stage=f"deep_research.{validated_context.stage.value}",
                prompt_version="deep-research-v1",
                trace_id=validated_context.trace_id,
            )
            return result.model_dump(mode="json")
        result = await self._run(
            "generate_solution",
            lambda client: generate_solution(validated_context, validated_snapshot, client),
        )
        return result.model_dump(mode="json")


class MockAIEngine:
    """Deterministic offline implementation with the same five output structures."""

    def __init__(self, *, recorder: InMemoryRunRecorder | None = None) -> None:
        self.recorder = recorder or InMemoryRunRecorder()

    @property
    def last_run(self) -> AIRunMetadata | None:
        return self.recorder.last_run

    def _record(self, method: str, timer: RunTimer) -> None:
        self.recorder.record(
            AIRunMetadata(
                trace_id=timer.trace_id,
                method=method,
                stage=method,
                status=AIRunStatus.SUCCEEDED,
                model_version="mock-v1",
                prompt_version=PROMPT_VERSIONS[method],
                schema_version=SCHEMA_VERSION,
                embedding_version=DEFAULT_EMBEDDING_VERSION,
                parameter_version="deterministic=true",
                started_at=timer.started_at,
                duration_ms=timer.duration_ms,
                model_attempt_count=0,
            )
        )

    async def extract_profile(
        self,
        raw_text: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        timer = RunTimer()
        raw_text = validate_raw_text(raw_text)
        if not any(item.strip() for item in source_ids):
            raise ValueError("source_ids must not be empty")
        profile = CustomerProfileDraft(
            customer_name="待确认客户",
            information_gaps=["客户名称、行业和具体目标待人工确认"],
            profile_status=ProfileStatus.PENDING_CONFIRMATION,
            profile_summary="这是离线 Mock 根据输入生成的待确认客户画像。",
            source_ids=[item.strip() for item in source_ids if item.strip()],
        )
        self._record("extract_profile", timer)
        return profile.model_dump(mode="json")

    async def extract_experience(self, raw_text: str, source_id: str) -> dict[str, Any]:
        timer = RunTimer()
        raw_text = validate_raw_text(raw_text)
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        excerpt = raw_text.strip()[:200]
        experience = ExperienceDraft(
            name="Mock 待校验经验",
            problem=excerpt,
            solution="待人工从原文确认方案做法。",
            source_id=source_id,
            embedding_text=f"适用问题：{excerpt}。主要方案：待人工从原文确认方案做法。",
        )
        self._record("extract_experience", timer)
        return experience.model_dump(mode="json")

    async def extract_capabilities(
        self,
        raw_text: str,
        source_id: str,
    ) -> list[dict[str, Any]]:
        timer = RunTimer()
        raw_text = validate_raw_text(raw_text)
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        capability = CapabilityDraft(
            name="Mock 待校验能力",
            description=raw_text.strip()[:200],
            input="待人工确认",
            output="待人工确认",
            limitations="Mock 不承诺真实企业能力，必须人工审核。",
            tags=["待校验"],
            source_id=source_id,
            embedding_text="能力名称：Mock 待校验能力。限制：必须人工审核。",
        )
        self._record("extract_capabilities", timer)
        return [capability.model_dump(mode="json")]

    async def extract_search_intent(self, context: dict[str, Any]) -> dict[str, Any]:
        timer = RunTimer()
        validated = SolutionContext.model_validate(compact_context_payload(context))
        if validated.task_type == "expert_routing":
            package = validated.research_context
            if package is None:
                raise ValueError("expert_routing requires research_context")
            recommendations = [
                CandidateRecommendation(
                    candidate_id=candidate.candidate_id,
                    reason=f"离线 Mock：候选人有贡献记录“{candidate.contributions[0].title}”",
                    contribution_ids=[candidate.contributions[0].contribution_id],
                )
                for candidate in validated.candidate_records[:3]
                if candidate.contributions
            ]
            intent = SearchIntent(
                current_requirement=validated.current_requirement,
                query_text=validated.current_requirement,
                business_goal=validated.current_requirement,
                industry=validated.customer_profile.industry,
                hard_constraints=validated.customer_profile.constraints,
                missing_information=package.knowledge_gaps,
                experience_query_text=f"专家缺口背景：{package.research_document_summary}",
                capability_query_text=f"待确认知识：{'、'.join(package.knowledge_gaps)}",
                ranking_signals=[
                    "direct_contribution_match",
                    "reviewed_contribution",
                    "contribution_freshness",
                ],
                expertise_gaps=package.knowledge_gaps,
                ranked_candidate_ids=[item.candidate_id for item in recommendations],
                candidate_reasons=recommendations,
                expert_questions=package.questions,
                do_not_invite_reason=(
                    None if recommendations else "后端未提供带真实贡献证据的候选人。"
                ),
                embedding_text=f"专家知识缺口：{'、'.join(package.knowledge_gaps)}",
            )
            self._record("extract_search_intent", timer)
            return intent.model_dump(mode="json")
        intent = SearchIntent(
            current_requirement=validated.current_requirement,
            query_text=validated.current_requirement,
            industry=validated.customer_profile.industry,
            goals=validated.customer_profile.goals,
            business_goal=next(iter(validated.customer_profile.goals), None),
            hard_constraints=validated.customer_profile.constraints,
            missing_information=validated.customer_profile.information_gaps,
            experience_query_text=f"历史相似问题：{validated.current_requirement}。",
            capability_query_text=f"当前所需能力：{validated.current_requirement}。",
            embedding_text=f"当前需求：{validated.current_requirement}。",
        )
        self._record("extract_search_intent", timer)
        return intent.model_dump(mode="json")

    async def generate_solution(
        self,
        context: dict[str, Any],
        retrieval_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        timer = RunTimer()
        validated_context = SolutionContext.model_validate(compact_context_payload(context))
        validated_snapshot = RetrievalSnapshot.model_validate(retrieval_snapshot)
        model_result = {
            "requirement_understanding": [
                {
                    "text": validated_context.current_requirement,
                    "boundary": "ai_inference",
                    "asset_id": None,
                    "source_id": None,
                }
            ],
            "initial_recommendations": [],
            "historical_evidence": [],
            "capability_composition": [],
            "prerequisites_and_risks": [],
            "pending_confirmations": [],
            "suggested_questions": ["还需要确认哪些会改变方案范围的关键信息？"],
        }
        solution: Solution = finalize_solution_result(
            validated_context,
            validated_snapshot,
            model_result,
        )
        if validated_context.mode.value == "deep":
            stage = validated_context.stage
            if stage is None or validated_context.research_task_id is None:
                raise ValueError("deep mode requires stage and research_task_id")
            plan = validated_context.research_plan
            if stage == ResearchStage.PLANNING and plan is None:
                plan = ResearchPlan(
                    objective=validated_context.current_requirement,
                    subquestions=[
                        ResearchSubquestion(
                            question_id="Q1",
                            question=validated_context.current_requirement,
                            completion_condition="已有证据足以支持结论或明确记录关键缺口",
                        )
                    ],
                    completion_conditions=["完成引用核验并列出仍待确认的信息"],
                )
            deep_result = DeepResearchStageResult(
                trace_id=validated_context.trace_id or str(uuid4()),
                research_task_id=validated_context.research_task_id,
                stage=stage,
                status=(
                    ResearchTaskStatus.COMPLETED
                    if stage == ResearchStage.FINAL_REPORT
                    else ResearchTaskStatus.RUNNING
                ),
                research_plan=plan,
                findings=validated_context.prior_findings,
                audit=(
                    ResearchAudit(
                        passed=not validated_snapshot.missing_information,
                        knowledge_gaps=validated_snapshot.missing_information,
                    )
                    if stage == ResearchStage.FACT_AUDIT
                    else None
                ),
                final_solution=(solution if stage == ResearchStage.FINAL_REPORT else None),
            )
            self._record("generate_solution", timer)
            return deep_result.model_dump(mode="json")
        self._record("generate_solution", timer)
        return solution.model_dump(mode="json")
