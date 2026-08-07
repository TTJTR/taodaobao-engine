from typing import Protocol

from app.ai.prompts.expert_routing import (
    EXPERT_ROUTING_SYSTEM_PROMPT,
    build_expert_routing_user_prompt,
)
from app.ai.schemas import (
    CandidateRecommendation,
    ExpertQuestion,
    SearchIntent,
    SolutionContext,
)


class JsonModelClient(Protocol):
    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


def empty_expert_routing_intent(context: SolutionContext, reason: str) -> SearchIntent:
    package = context.research_context
    if package is None:
        raise ValueError("expert routing requires a research context package")
    return SearchIntent(
        current_requirement=context.current_requirement,
        query_text=context.current_requirement,
        business_goal=context.current_requirement,
        industry=context.customer_profile.industry,
        hard_constraints=context.customer_profile.constraints,
        preferred_tags=[],
        excluded_claims=[],
        missing_information=package.knowledge_gaps,
        experience_query_text=f"专家缺口背景：{package.research_document_summary}",
        capability_query_text=f"待确认知识：{'、'.join(package.knowledge_gaps)}",
        ranking_signals=[
            "direct_contribution_match",
            "reviewed_contribution",
            "contribution_freshness",
        ],
        expertise_gaps=package.knowledge_gaps,
        expert_questions=package.questions,
        do_not_invite_reason=reason,
        embedding_text=f"专家知识缺口：{'、'.join(package.knowledge_gaps)}",
    )


async def route_experts(
    context: SolutionContext,
    model_client: JsonModelClient,
) -> SearchIntent:
    if context.task_type != "expert_routing" or context.research_context is None:
        raise ValueError("route_experts requires expert_routing context")
    if not context.candidate_records:
        return empty_expert_routing_intent(context, "后端未提供真实候选人，不生成或邀请专家。")

    result = await model_client.generate_json(
        EXPERT_ROUTING_SYSTEM_PROMPT,
        build_expert_routing_user_prompt(context.model_dump_json(indent=2)),
    )
    if set(result) != {
        "expertise_gaps",
        "ranked_candidates",
        "questions",
        "do_not_invite_reason",
    }:
        raise ValueError("expert routing model output has unexpected fields")
    if not isinstance(result["expertise_gaps"], list) or not all(
        isinstance(item, str) for item in result["expertise_gaps"]
    ):
        raise ValueError("expert routing expertise_gaps must be a list of strings")
    if not isinstance(result["ranked_candidates"], list):
        raise ValueError("expert routing ranked_candidates must be a list")
    if not isinstance(result["questions"], list):
        raise ValueError("expert routing questions must be a list")

    candidates = {candidate.candidate_id: candidate for candidate in context.candidate_records}
    recommendations = [
        CandidateRecommendation.model_validate(item) for item in result["ranked_candidates"]
    ]
    if len(recommendations) > 3:
        raise ValueError("expert routing may recommend at most three candidates")
    for recommendation in recommendations:
        candidate = candidates.get(recommendation.candidate_id)
        if candidate is None:
            raise ValueError("expert routing invented a candidate_id")
        allowed_contribution_ids = {
            contribution.contribution_id for contribution in candidate.contributions
        }
        if not set(recommendation.contribution_ids).issubset(allowed_contribution_ids):
            raise ValueError("expert routing cited another or invented contribution")

    package = context.research_context
    questions = [ExpertQuestion.model_validate(item) for item in result["questions"]]
    expertise_gaps = list(dict.fromkeys(result["expertise_gaps"]))
    return SearchIntent(
        current_requirement=context.current_requirement,
        query_text=context.current_requirement,
        business_goal=context.current_requirement,
        industry=context.customer_profile.industry,
        hard_constraints=context.customer_profile.constraints,
        preferred_tags=[],
        excluded_claims=[],
        missing_information=expertise_gaps,
        experience_query_text=f"专家缺口背景：{package.research_document_summary}",
        capability_query_text=f"待确认知识：{'、'.join(expertise_gaps)}",
        ranking_signals=[
            "direct_contribution_match",
            "reviewed_contribution",
            "contribution_freshness",
        ],
        expertise_gaps=expertise_gaps,
        ranked_candidate_ids=[item.candidate_id for item in recommendations],
        candidate_reasons=recommendations,
        expert_questions=questions,
        do_not_invite_reason=result["do_not_invite_reason"],
        embedding_text=f"专家知识缺口：{'、'.join(expertise_gaps)}",
    )
