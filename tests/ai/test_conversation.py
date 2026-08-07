from app.ai.conversation import compact_context_payload
from app.ai.schemas import SolutionContext


def make_long_context() -> dict:
    return {
        "customer_profile": {
            "customer_name": "测试客户",
            "industry": "制造业",
            "background": None,
            "current_problems": [],
            "goals": [],
            "constraints": ["不能替换主系统"],
            "existing_systems": [],
            "information_gaps": [],
            "profile_status": "confirmed",
            "profile_summary": "测试画像",
            "source_ids": ["SRC-001"],
        },
        "current_requirement": "当前这一问必须完整保留",
        "conversation_history": [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"第{index}条旧消息"}
            for index in range(30)
        ],
    }


def test_long_conversation_is_compacted_without_changing_current_request_or_profile() -> None:
    raw = make_long_context()

    compacted = compact_context_payload(raw)
    validated = SolutionContext.model_validate(compacted)

    assert validated.current_requirement == "当前这一问必须完整保留"
    assert validated.customer_profile.constraints == ["不能替换主系统"]
    assert len(validated.conversation_history) == 20
    assert validated.conversation_history[0].content.startswith("较早 11 条对话的压缩摘要")
    assert validated.conversation_history[-1].content == "第29条旧消息"


def test_short_conversation_is_left_unchanged() -> None:
    raw = make_long_context()
    raw["conversation_history"] = raw["conversation_history"][:2]

    assert compact_context_payload(raw) == raw
