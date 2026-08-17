from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIRunRecord,
    HtmlArtifact,
    PresentationInputSnapshot,
    ProcessStatus,
    WorkflowTask,
    WorkflowTaskStatus,
)

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "total_tokens",
)


def merge_token_usage(target: dict[str, int], usage: dict[str, Any] | None) -> None:
    if not isinstance(usage, dict):
        return
    for field in TOKEN_FIELDS:
        value = usage.get(field)
        if isinstance(value, int | float) and value >= 0:
            target[field] = target.get(field, 0) + int(value)


def normalized_tokens(usage: dict[str, int]) -> dict[str, int]:
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
    return {
        **usage,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def summarize_usage_rows(
    search_tasks: list[WorkflowTask],
    html_artifacts: list[HtmlArtifact],
    failed_html_tasks: list[WorkflowTask],
    *,
    window_start: datetime,
    window_end: datetime,
    ai_runs: list[AIRunRecord] | None = None,
) -> dict[str, Any]:
    ai_runs = ai_runs or []
    bailian_usage: dict[str, int] = {}
    deepseek_usage: dict[str, int] = {}
    recent: list[dict[str, Any]] = []

    for task in search_tasks:
        usage = task.payload.get("usage") if isinstance(task.payload, dict) else None
        merge_token_usage(bailian_usage, usage)
        event_at = task.finished_at or task.updated_at or task.created_at
        recent.append(
            {
                "provider": "aliyun_bailian",
                "feature": "intelligence_search",
                "status": task.status.value,
                "tokens": normalized_tokens(dict(usage or {})).get("total_tokens", 0),
                "at": event_at.isoformat(),
                "trace_id": task.trace_id,
            }
        )

    for run in ai_runs:
        recent.append(
            {
                "provider": "aliyun_bailian",
                "feature": f"{run.target_type}.{run.method}",
                "status": run.status.value,
                "tokens": None,
                "at": run.created_at.isoformat(),
                "trace_id": run.trace_id,
                "duration_ms": run.duration_ms,
                "model": run.model_version,
            }
        )

    for artifact in html_artifacts:
        usage = artifact.render_report.get("usage", {})
        merge_token_usage(deepseek_usage, usage)
        recent.append(
            {
                "provider": "deepseek",
                "feature": "interactive_html",
                "status": artifact.status,
                "tokens": normalized_tokens(dict(usage or {})).get("total_tokens", 0),
                "at": artifact.created_at.isoformat(),
                "trace_id": None,
            }
        )

    for task in failed_html_tasks:
        event_at = task.finished_at or task.updated_at or task.created_at
        recent.append(
            {
                "provider": "deepseek",
                "feature": "interactive_html",
                "status": "failed",
                "tokens": None,
                "at": event_at.isoformat(),
                "trace_id": task.trace_id,
            }
        )

    recent.sort(key=lambda row: row["at"], reverse=True)
    search_failed = sum(task.status == WorkflowTaskStatus.FAILED for task in search_tasks)
    ai_failed = sum(run.status == ProcessStatus.FAILED for run in ai_runs)
    ai_durations = [run.duration_ms for run in ai_runs if run.duration_ms is not None]
    metered_search_calls = sum(
        isinstance(task.payload, dict) and isinstance(task.payload.get("usage"), dict)
        and bool(task.payload["usage"])
        for task in search_tasks
    )
    unmetered_calls = len(ai_runs) + len(search_tasks) - metered_search_calls
    return {
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "providers": {
            "aliyun_bailian": {
                "calls": len(search_tasks) + len(ai_runs),
                "successful_calls": len(search_tasks) - search_failed + len(ai_runs) - ai_failed,
                "failed_calls": search_failed + ai_failed,
                "usage": normalized_tokens(bailian_usage),
                "known_token_calls": metered_search_calls,
                "unmetered_calls": unmetered_calls,
                "average_duration_ms": (
                    round(sum(ai_durations) / len(ai_durations)) if ai_durations else None
                ),
                "source": ["workflow_tasks.payload.usage", "ai_run_records"],
                "usage_note": "Token 仅统计公开情报搜索；其余 AI 调用按审计记录计次。",
            },
            "deepseek": {
                "calls": len(html_artifacts) + len(failed_html_tasks),
                "successful_calls": len(html_artifacts),
                "failed_calls": len(failed_html_tasks),
                "usage": normalized_tokens(deepseek_usage),
                "source": "html_artifacts.render_report.usage",
            },
        },
        "recent_events": recent[:50],
    }


class UsageMonitorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary(self, hours: int) -> dict[str, Any]:
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(hours=hours)
        search_tasks = list(
            (
                await self.session.scalars(
                    select(WorkflowTask).where(
                        WorkflowTask.kind == "intelligence_search",
                        WorkflowTask.created_at >= window_start,
                    )
                )
            ).all()
        )
        ai_runs = list(
            (
                await self.session.scalars(
                    select(AIRunRecord).where(
                        AIRunRecord.created_at >= window_start,
                        AIRunRecord.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        html_artifacts = list(
            (
                await self.session.scalars(
                    select(HtmlArtifact).where(
                        HtmlArtifact.provider_mode == "interactive-live",
                        HtmlArtifact.created_at >= window_start,
                    )
                )
            ).all()
        )
        failed_html_tasks = list(
            (
                await self.session.scalars(
                    select(WorkflowTask)
                    .join(
                        PresentationInputSnapshot,
                        PresentationInputSnapshot.presentation_id == WorkflowTask.target_id,
                    )
                    .where(
                        WorkflowTask.kind == "presentation_render",
                        WorkflowTask.status == WorkflowTaskStatus.FAILED,
                        WorkflowTask.created_at >= window_start,
                        WorkflowTask.is_deleted.is_(False),
                        PresentationInputSnapshot.snapshot_data["render_mode"].as_string()
                        == "interactive",
                        PresentationInputSnapshot.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        return summarize_usage_rows(
            search_tasks,
            html_artifacts,
            failed_html_tasks,
            window_start=window_start,
            window_end=window_end,
            ai_runs=ai_runs,
        )
