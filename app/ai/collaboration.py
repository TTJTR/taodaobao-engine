from collections.abc import Iterable

from app.ai.schemas import (
    CandidateRecommendation,
    CollaborationContentBundle,
    CollaborationDocumentItem,
    CollaborationDocumentSection,
    EvidenceBoundary,
    ExpertAnswerInput,
    ExpertQuestion,
    ExpertReplySummary,
    FeishuCardDraft,
    FeishuDocumentDraft,
    FeishuGroupOpeningDraft,
    ResearchContextPackage,
    Solution,
)

DOCUMENT_SECTION_TITLES = (
    ("requirement_understanding", "需求理解"),
    ("initial_recommendations", "首要建议"),
    ("historical_evidence", "历史经验依据"),
    ("capability_composition", "企业能力组合"),
    ("prerequisites_and_risks", "前置条件与风险"),
    ("pending_confirmations", "待确认事项"),
    ("sources", "资料来源"),
    ("suggested_questions", "建议追问"),
)


def truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def unique_texts(values: Iterable[str], limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            output.append(normalized)
            seen.add(normalized)
        if len(output) >= limit:
            break
    return output


def validate_collaboration_sources(
    research_context: ResearchContextPackage,
    solution: Solution,
) -> None:
    snapshot_pairs = {
        (item.asset_id, item.source_id)
        for item in [
            *research_context.evidence_snapshot.experiences,
            *research_context.evidence_snapshot.capabilities,
        ]
    }
    solution_pairs = {(source.asset_id, source.source_id) for source in solution.sources}
    if not solution_pairs.issubset(snapshot_pairs):
        raise ValueError("solution contains a source outside the research evidence snapshot")


def document_sections(solution: Solution) -> list[CollaborationDocumentSection]:
    sections: list[CollaborationDocumentSection] = []
    for key, title in DOCUMENT_SECTION_TITLES:
        if key == "sources":
            items = [
                CollaborationDocumentItem(
                    text=f"{source.title}（{source.asset_id}/{source.source_id}）",
                    asset_id=source.asset_id,
                    source_id=source.source_id,
                )
                for source in solution.sources
            ]
        elif key == "suggested_questions":
            items = [CollaborationDocumentItem(text=text) for text in solution.suggested_questions]
        else:
            items = [
                CollaborationDocumentItem(
                    text=item.text,
                    boundary=item.boundary,
                    asset_id=item.asset_id,
                    source_id=item.source_id,
                )
                for item in getattr(solution, key)
            ]
        sections.append(CollaborationDocumentSection(key=key, title=title, items=items))
    return sections


def select_questions(
    research_context: ResearchContextPackage,
    selected_question_ids: list[str] | None,
) -> list[ExpertQuestion]:
    questions = {question.question_id: question for question in research_context.questions}
    if selected_question_ids is None:
        return list(questions.values())
    unknown = set(selected_question_ids) - set(questions)
    if unknown:
        raise ValueError("selected expert question is outside the research context")
    return [questions[question_id] for question_id in selected_question_ids]


def render_collaboration_content(
    research_context: ResearchContextPackage,
    solution: Solution,
    recommendations: list[CandidateRecommendation],
    document_url: str,
    *,
    selected_question_ids: list[str] | None = None,
    fun_opening_enabled: bool = True,
    fun_opening_line: str = "先让证据开口，再请专家补上最后一块拼图。",
) -> CollaborationContentBundle:
    validate_collaboration_sources(research_context, solution)
    if len(recommendations) > 3:
        raise ValueError("collaboration may include at most three recommended experts")
    questions = select_questions(research_context, selected_question_ids)

    conclusion_candidates = [
        *(item.text for item in solution.initial_recommendations),
        *(item.text for item in solution.requirement_understanding),
    ]
    conclusion = truncate(
        next(iter(conclusion_candidates), "研究结论已整理，等待人工审核。"),
        160,
    )
    key_points = unique_texts(
        [
            *(item.text for item in solution.prerequisites_and_risks),
            *(item.text for item in solution.pending_confirmations),
            *(recommendation.reason for recommendation in recommendations),
        ],
        3,
    )

    document = FeishuDocumentDraft(
        research_task_id=research_context.research_task_id,
        title=f"售前研究报告 · {research_context.research_task_id}",
        sections=document_sections(solution),
        sources=solution.sources,
    )
    card = FeishuCardDraft(
        research_task_id=research_context.research_task_id,
        title="售前研究需要你确认",
        conclusion=conclusion,
        key_points=key_points,
        status="awaiting_expert" if questions else "ready_for_review",
        document_url=document_url,
    )

    opening_parts: list[str] = []
    if fun_opening_enabled and fun_opening_line.strip():
        opening_parts.append(fun_opening_line.strip())
    opening_parts.extend(
        [
            f"研究任务：{research_context.research_task_id}",
            f"客户背景：{research_context.profile_summary}",
            "协作目的：补齐研究中的关键知识缺口，不直接改写企业事实。",
        ]
    )
    for recommendation in recommendations:
        opening_parts.append(f"邀请建议 {recommendation.candidate_id}：{recommendation.reason}")
    for question in questions:
        opening_parts.append(f"问题 {question.question_id}：{question.question}")
    opening_parts.append(f"完整研究：{document_url}")
    group_opening = FeishuGroupOpeningDraft(
        research_task_id=research_context.research_task_id,
        text="\n".join(opening_parts),
        candidate_ids=[item.candidate_id for item in recommendations],
        question_ids=[question.question_id for question in questions],
        document_url=document_url,
    )
    return CollaborationContentBundle(
        document=document,
        card=card,
        group_opening=group_opening,
    )


def render_from_one_click(*args: object, **kwargs: object) -> CollaborationContentBundle:
    return render_collaboration_content(*args, **kwargs)  # type: ignore[arg-type]


def render_from_task_selector(*args: object, **kwargs: object) -> CollaborationContentBundle:
    return render_collaboration_content(*args, **kwargs)  # type: ignore[arg-type]


def summarize_expert_reply(answer: ExpertAnswerInput) -> ExpertReplySummary:
    return ExpertReplySummary(
        research_task_id=answer.research_task_id,
        question_id=answer.question_id,
        author_id=answer.author_id,
        author_name=answer.author_name,
        message_url=answer.message_url,
        summary=truncate(answer.answer_text, 500),
        boundary=EvidenceBoundary.PENDING_CONFIRMATION,
    )
