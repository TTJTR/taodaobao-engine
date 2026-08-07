from datetime import UTC, datetime

from app.ai.collaboration import (
    render_from_one_click,
    render_from_task_selector,
    summarize_expert_reply,
)
from app.ai.schemas import (
    CandidateRecommendation,
    CitedItem,
    EvidenceBoundary,
    ExpertAnswerInput,
    ExpertQuestion,
    ResearchContextPackage,
    RetrievalSnapshot,
    Solution,
)


def make_research_context() -> ResearchContextPackage:
    return ResearchContextPackage(
        research_task_id="RESEARCH-001",
        conversation_summary="客户希望做小范围试点。",
        research_document_summary="证据支持旁路试点，接口权限待确认。",
        profile_summary="制造业客户，不允许影响主系统。",
        evidence_snapshot=RetrievalSnapshot(
            experiences=[],
            capabilities=[],
            created_at=datetime.now(UTC),
        ),
        knowledge_gaps=["接口权限"],
        questions=[
            ExpertQuestion(
                question_id="Q1",
                question="开放写入接口需要哪些安全审批？",
                required_roles=["接口安全"],
            )
        ],
    )


def make_solution() -> Solution:
    return Solution(
        requirement_understanding=[
            CitedItem(text="客户希望小范围试点。", boundary=EvidenceBoundary.AI_INFERENCE)
        ],
        initial_recommendations=[
            CitedItem(text="建议先做只读旁路验证。", boundary=EvidenceBoundary.AI_INFERENCE)
        ],
        prerequisites_and_risks=[
            CitedItem(text="接口权限尚未确认。", boundary=EvidenceBoundary.PENDING_CONFIRMATION)
        ],
        pending_confirmations=[
            CitedItem(text="安全审批流程待确认。", boundary=EvidenceBoundary.PENDING_CONFIRMATION)
        ],
        suggested_questions=["谁负责接口安全审批？"],
    )


def make_recommendations() -> list[CandidateRecommendation]:
    return [
        CandidateRecommendation(
            candidate_id="USER-001",
            reason="贡献过接口安全评审文档",
            contribution_ids=["CONTRIB-001"],
        )
    ]


def test_two_collaboration_entry_points_render_identical_content() -> None:
    args = (
        make_research_context(),
        make_solution(),
        make_recommendations(),
        "https://example.test/document/1",
    )

    one_click = render_from_one_click(*args)
    selector = render_from_task_selector(*args)

    assert one_click == selector
    assert [section.key for section in one_click.document.sections] == [
        "requirement_understanding",
        "initial_recommendations",
        "historical_evidence",
        "capability_composition",
        "prerequisites_and_risks",
        "pending_confirmations",
        "sources",
        "suggested_questions",
    ]
    assert len(one_click.card.key_points) <= 3
    assert len(one_click.card.conclusion) <= 160


def test_fun_opening_is_optional_and_appears_only_once() -> None:
    context = make_research_context()
    solution = make_solution()
    recommendations = make_recommendations()
    opening = "先让证据开口。"

    enabled = render_from_one_click(
        context,
        solution,
        recommendations,
        "https://example.test/document/1",
        fun_opening_line=opening,
    )
    disabled = render_from_one_click(
        context,
        solution,
        recommendations,
        "https://example.test/document/1",
        fun_opening_enabled=False,
        fun_opening_line=opening,
    )

    assert enabled.group_opening.text.count(opening) == 1
    assert opening not in disabled.group_opening.text


def test_expert_reply_summary_keeps_task_question_author_and_message_link() -> None:
    answer = ExpertAnswerInput(
        research_task_id="RESEARCH-001",
        question_id="Q1",
        author_id="USER-001",
        author_name="李专家",
        message_url="https://example.test/message/1",
        answer_text="需要先完成接口权限和安全审批。",
    )

    summary = summarize_expert_reply(answer)

    assert summary.research_task_id == "RESEARCH-001"
    assert summary.question_id == "Q1"
    assert summary.author_name == "李专家"
    assert summary.message_url == "https://example.test/message/1"
    assert summary.boundary == "pending_confirmation"
