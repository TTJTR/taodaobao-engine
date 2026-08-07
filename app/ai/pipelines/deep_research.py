from typing import Protocol
from uuid import uuid4

from app.ai.pipelines.solution import generate_solution
from app.ai.prompts.deep_research import (
    DEEP_RESEARCH_SYSTEM_PROMPT,
    build_deep_research_user_prompt,
)
from app.ai.schemas import (
    DeepResearchStageResult,
    EvidenceBoundary,
    ExpertQuestion,
    ResearchAudit,
    ResearchContextPackage,
    ResearchFinding,
    ResearchPlan,
    ResearchRoute,
    ResearchStage,
    ResearchTaskStatus,
    RetrievalSnapshot,
    SolutionContext,
)


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


MODEL_FIELDS = {"research_plan", "findings", "routes", "audit", "expert_questions"}


def allowed_research_citations(
    snapshot: RetrievalSnapshot,
) -> dict[tuple[str, str], EvidenceBoundary]:
    allowed = {
        (item.asset_id, item.source_id): EvidenceBoundary.HISTORICAL_FACT
        for item in snapshot.experiences
    }
    allowed.update(
        {
            (item.asset_id, item.source_id): EvidenceBoundary.ENTERPRISE_CAPABILITY
            for item in snapshot.capabilities
        }
    )
    return allowed


def validate_findings(
    raw_findings: object,
    stage: ResearchStage,
    snapshot: RetrievalSnapshot,
) -> list[ResearchFinding]:
    if not isinstance(raw_findings, list):
        raise ValueError("deep research findings must be a list")
    allowed = allowed_research_citations(snapshot)
    findings: list[ResearchFinding] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            raise ValueError("each research finding must be an object")
        finding = ResearchFinding.model_validate({**raw_finding, "stage": stage})
        if finding.asset_id and finding.source_id:
            expected = allowed.get((finding.asset_id, finding.source_id))
            if expected != finding.boundary:
                raise ValueError("research finding citation is outside the snapshot or wrong type")
        findings.append(finding)
    return findings


def validate_routes(raw_routes: object, snapshot: RetrievalSnapshot) -> list[ResearchRoute]:
    if not isinstance(raw_routes, list):
        raise ValueError("deep research routes must be a list")
    routes = [ResearchRoute.model_validate(route) for route in raw_routes]
    allowed_asset_ids = {item.asset_id for item in [*snapshot.experiences, *snapshot.capabilities]}
    for route in routes:
        if not set(route.supporting_asset_ids).issubset(allowed_asset_ids):
            raise ValueError("research route contains an asset outside the snapshot")
    return routes


def conversation_summary(context: SolutionContext) -> str:
    if not context.conversation_history:
        return context.current_requirement
    snippets = [
        f"{message.role}: {message.content[:160]}" for message in context.conversation_history[-8:]
    ]
    return "；".join(snippets)


def research_document_summary(
    context: SolutionContext,
    findings: list[ResearchFinding],
    routes: list[ResearchRoute],
) -> str:
    parts = [context.current_requirement]
    parts.extend(finding.text for finding in findings[-6:])
    parts.extend(f"路线 {route.name}：{route.summary}" for route in routes)
    return "\n".join(parts)[:4000]


def build_research_context_package(
    context: SolutionContext,
    snapshot: RetrievalSnapshot,
    findings: list[ResearchFinding],
    routes: list[ResearchRoute],
    knowledge_gaps: list[str],
    questions: list[ExpertQuestion],
) -> ResearchContextPackage:
    if not context.research_task_id:
        raise ValueError("research_task_id is required")
    return ResearchContextPackage(
        research_task_id=context.research_task_id,
        conversation_summary=conversation_summary(context),
        research_document_summary=research_document_summary(context, findings, routes),
        profile_summary=context.customer_profile.profile_summary,
        evidence_snapshot=snapshot,
        knowledge_gaps=knowledge_gaps,
        questions=questions,
    )


def expert_answer_findings(context: SolutionContext) -> list[ResearchFinding]:
    if not context.stage:
        raise ValueError("deep research stage is required")
    findings: list[ResearchFinding] = []
    for index, answer in enumerate(context.expert_answers, start=1):
        if answer.research_task_id != context.research_task_id:
            raise ValueError("expert answer belongs to a different research task")
        findings.append(
            ResearchFinding(
                finding_id=f"expert-answer-{index}",
                text=(
                    f"专家 {answer.author_name} 对问题 {answer.question_id} 的待核实回复："
                    f"{answer.answer_text}（原消息：{answer.message_url}）"
                ),
                boundary=EvidenceBoundary.PENDING_CONFIRMATION,
                stage=context.stage,
            )
        )
    return findings


async def generate_deep_research_stage(
    context: SolutionContext,
    snapshot: RetrievalSnapshot,
    model_client: JsonModelClient,
) -> DeepResearchStageResult:
    if not context.research_task_id or not context.stage:
        raise ValueError("deep research requires research_task_id and stage")
    trace_id = context.trace_id or str(uuid4())

    if context.stage == ResearchStage.AWAITING_EXPERT:
        answer_findings = expert_answer_findings(context)
        status = (
            ResearchTaskStatus.RUNNING if answer_findings else ResearchTaskStatus.AWAITING_EXPERT
        )
        return DeepResearchStageResult(
            trace_id=trace_id,
            research_task_id=context.research_task_id,
            stage=context.stage,
            status=status,
            research_plan=context.research_plan,
            findings=[*context.prior_findings, *answer_findings],
        )

    if context.stage == ResearchStage.FINAL_REPORT:
        solution = await generate_solution(context, snapshot, model_client)
        return DeepResearchStageResult(
            trace_id=trace_id,
            research_task_id=context.research_task_id,
            stage=context.stage,
            status=ResearchTaskStatus.COMPLETED,
            research_plan=context.research_plan,
            findings=context.prior_findings,
            final_solution=solution,
        )

    result = await model_client.generate_json(
        DEEP_RESEARCH_SYSTEM_PROMPT,
        build_deep_research_user_prompt(
            context.stage.value,
            context.model_dump_json(indent=2),
            snapshot.model_dump_json(indent=2),
        ),
    )
    if set(result) != MODEL_FIELDS:
        raise ValueError("deep research model output has unexpected fields")

    plan = (
        ResearchPlan.model_validate(result["research_plan"])
        if result["research_plan"] is not None
        else context.research_plan
    )
    if context.stage == ResearchStage.PLANNING and plan is None:
        raise ValueError("planning stage must return a research_plan")
    findings = validate_findings(result["findings"], context.stage, snapshot)
    routes = validate_routes(result["routes"], snapshot)
    audit = ResearchAudit.model_validate(result["audit"]) if result["audit"] else None
    if context.stage == ResearchStage.FACT_AUDIT and audit is None:
        raise ValueError("fact_audit stage must return an audit")
    if not isinstance(result["expert_questions"], list):
        raise ValueError("deep research expert_questions must be a list")
    questions = [ExpertQuestion.model_validate(item) for item in result["expert_questions"]]
    knowledge_gaps = audit.knowledge_gaps if audit else []
    needs_expert = bool(knowledge_gaps or questions)
    package = (
        build_research_context_package(
            context,
            snapshot,
            [*context.prior_findings, *findings],
            routes,
            knowledge_gaps,
            questions,
        )
        if needs_expert
        else None
    )
    return DeepResearchStageResult(
        trace_id=trace_id,
        research_task_id=context.research_task_id,
        stage=context.stage,
        status=(ResearchTaskStatus.AWAITING_EXPERT if needs_expert else ResearchTaskStatus.RUNNING),
        research_plan=plan,
        findings=[*context.prior_findings, *findings],
        routes=routes,
        audit=audit,
        expert_context=package,
    )
