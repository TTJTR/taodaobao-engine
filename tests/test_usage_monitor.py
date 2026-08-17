from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.db.models import WorkflowTaskStatus
from app.services.usage_monitor_service import merge_token_usage, summarize_usage_rows

NOW = datetime(2026, 8, 15, 20, 0, tzinfo=UTC)


def _task(status, usage=None, *, kind="intelligence_search", trace_id="trace-1"):
    return SimpleNamespace(
        kind=kind,
        status=status,
        payload={"usage": usage or {}},
        finished_at=NOW,
        updated_at=NOW,
        created_at=NOW,
        trace_id=trace_id,
    )


def _artifact(usage):
    return SimpleNamespace(
        render_report={"usage": usage},
        status="ready",
        created_at=NOW,
    )


def _ai_run(status, *, method="extract_profile", target_type="customer_profile"):
    return SimpleNamespace(
        status=status,
        method=method,
        target_type=target_type,
        created_at=NOW,
        trace_id="ai-trace-1",
        duration_ms=1250,
        model_version="qwen-plus",
    )


def test_merge_token_usage_accumulates_continuation_requests():
    total = {}
    merge_token_usage(total, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
    merge_token_usage(total, {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13})
    assert total == {"prompt_tokens": 15, "completion_tokens": 28, "total_tokens": 43}


def test_summary_separates_bailian_and_deepseek_usage():
    result = summarize_usage_rows(
        [
            _task(
                WorkflowTaskStatus.COMPLETED,
                {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130},
            ),
            _task(WorkflowTaskStatus.FAILED, trace_id="trace-2"),
        ],
        [_artifact({"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280})],
        [_task(WorkflowTaskStatus.FAILED, kind="presentation_render", trace_id="trace-3")],
        window_start=NOW,
        window_end=NOW,
        ai_runs=[_ai_run(WorkflowTaskStatus.COMPLETED)],
    )
    assert result["providers"]["aliyun_bailian"]["calls"] == 3
    assert result["providers"]["aliyun_bailian"]["failed_calls"] == 1
    assert result["providers"]["aliyun_bailian"]["usage"]["total_tokens"] == 130
    assert result["providers"]["aliyun_bailian"]["known_token_calls"] == 1
    assert result["providers"]["aliyun_bailian"]["unmetered_calls"] == 2
    assert result["providers"]["aliyun_bailian"]["average_duration_ms"] == 1250
    assert result["providers"]["deepseek"]["calls"] == 2
    assert result["providers"]["deepseek"]["usage"]["total_tokens"] == 280
    failed_event = next(
        event
        for event in result["recent_events"]
        if event["provider"] == "deepseek" and event["status"] == "failed"
    )
    assert failed_event["tokens"] is None


def test_monitor_token_rejects_unconfigured_and_invalid(monkeypatch):
    from app.api.v1.routes import usage_monitor

    monkeypatch.setattr(usage_monitor.settings, "usage_monitor_token", None)
    with pytest.raises(AppError) as unconfigured:
        usage_monitor.require_monitor_token("x")
    assert unconfigured.value.status_code == 503

    monkeypatch.setattr(usage_monitor.settings, "usage_monitor_token", "a" * 32)
    with pytest.raises(AppError) as invalid:
        usage_monitor.require_monitor_token("b" * 32)
    assert invalid.value.status_code == 401
    usage_monitor.require_monitor_token("a" * 32)
