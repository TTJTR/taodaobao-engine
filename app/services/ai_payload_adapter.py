from typing import Any


def build_solution_context(requirement: str, profile_snapshot: dict[str, Any]) -> dict:
    profile_data = profile_snapshot.get("profile") or {}
    customer_name = str(
        profile_snapshot.get("customer_name")
        or profile_data.get("customer_name")
        or "待确认客户"
    )
    source_ids = profile_data.get("source_ids") or profile_snapshot.get("source_ids") or []
    if not source_ids:
        snapshot_id = profile_snapshot.get("id", "unknown")
        source_ids = [f"profile-snapshot:{snapshot_id}"]
    summary = str(
        profile_data.get("profile_summary")
        or profile_data.get("background")
        or f"{customer_name}的客户画像，细节仍需在会话中确认。"
    )
    return {
        "customer_profile": {
            "customer_name": customer_name,
            "industry": profile_data.get("industry"),
            "background": profile_data.get("background"),
            "current_problems": _string_list(profile_data.get("current_problems")),
            "goals": _string_list(profile_data.get("goals")),
            "constraints": _string_list(profile_data.get("constraints")),
            "existing_systems": _string_list(profile_data.get("existing_systems")),
            "information_gaps": _string_list(profile_data.get("information_gaps")),
            "profile_status": profile_snapshot.get("status", "pending_confirmation"),
            "profile_summary": summary,
            "source_ids": [str(item) for item in source_ids],
        },
        "current_requirement": requirement,
    }


def normalize_retrieval_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiences": [
            _normalize_experience(item, rank)
            for rank, item in enumerate(snapshot.get("experiences", []), start=1)
        ],
        "capabilities": [
            _normalize_capability(item, rank)
            for rank, item in enumerate(snapshot.get("capabilities", []), start=1)
        ],
        "conflicts": snapshot.get("conflicts", []),
        "missing_information": snapshot.get("missing_information", []),
        "gap_summary": snapshot.get("gap_summary"),
        "can_generate_solution": snapshot.get("can_generate_solution", True),
        "created_at": snapshot["created_at"],
    }


def _normalize_experience(item: dict[str, Any], rank: int) -> dict[str, Any]:
    data = dict(item.get("data") or {})
    source_id = str(item["source_id"])
    name = str(data.get("name") or data.get("title") or "已审核经验")
    data.update(
        {
            "name": name,
            "applicable_problem": str(
                data.get("applicable_problem") or data.get("problem") or name
            ),
            "solution": str(data.get("solution") or data.get("description") or name),
            "source_id": source_id,
        }
    )
    for field in ("prerequisites", "risks", "applicable_conditions"):
        if field in data:
            data[field] = _optional_text(data[field])
    return {
        "asset_id": str(item.get("asset_id") or item["id"]),
        "source_id": source_id,
        "rank": rank,
        "match_reasons": item.get("match_reasons") or ["关键词匹配与更新时间排序"],
        "data": data,
    }


def _normalize_capability(item: dict[str, Any], rank: int) -> dict[str, Any]:
    data = dict(item.get("data") or {})
    source_id = str(item["source_id"])
    name = str(data.get("name") or data.get("title") or "已审核能力")
    data.update(
        {
            "name": name,
            "description": str(data.get("description") or data.get("solution") or name),
            "source_id": source_id,
        }
    )
    for field in ("prerequisites", "limitations"):
        if field in data:
            data[field] = _optional_text(data[field])
    return {
        "asset_id": str(item.get("asset_id") or item["id"]),
        "source_id": source_id,
        "rank": rank,
        "match_reasons": item.get("match_reasons") or ["关键词匹配与更新时间排序"],
        "data": data,
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts) or None
    text = str(value).strip()
    return text or None
