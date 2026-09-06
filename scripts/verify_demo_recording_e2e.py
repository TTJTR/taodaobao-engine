"""Verify the recording demo against a running deployment.

Run this inside the application container.  It creates an authenticated test
session from an existing workspace user, verifies that the prepared quick
solution can be restored, then creates (or reuses) the interactive HTML that
belongs to the completed Deep Research task.

The script never prints cookies, tokens, provider keys, or invitation codes.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.security import SessionCodec
from app.db.database import get_session_factory
from app.db.models import PresentationRun, PresentationStatus, User

API_BASE = "http://127.0.0.1:8000/api/v1"
QUICK_SESSION_ID = uuid.UUID("4aafb330-c788-41da-a4da-706e1ba112fd")
RESEARCH_TASK_ID = uuid.UUID("8f72b927-65a7-4a6f-9cdd-76981fddd4ca")
TERMINAL_PRESENTATION_STATUSES = {"ready", "needs_review", "failed", "stale", "blocked"}


def payload(response: httpx.Response) -> dict:
    response.raise_for_status()
    body = response.json()
    return body.get("data", body)


def collection_size(value: object) -> int | str:
    return len(value) if isinstance(value, (list, dict, str)) else type(value).__name__


async def auth_cookie_and_existing_presentation() -> tuple[str, uuid.UUID | None]:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.workspace_id == settings.demo_workspace_id)
            .order_by(User.created_at.asc())
        )
        if user is None:
            raise RuntimeError("demo workspace has no authenticated user")
        existing = await session.scalar(
            select(PresentationRun)
            .where(
                PresentationRun.workspace_id == settings.demo_workspace_id,
                PresentationRun.research_task_id == RESEARCH_TASK_ID,
                PresentationRun.status == PresentationStatus.READY,
            )
            .order_by(PresentationRun.created_at.desc())
        )
        token = SessionCodec(settings.session_secret, settings.session_ttl_seconds).encode(
            user.id, user.workspace_id
        )
        return token, existing.id if existing else None


async def main() -> None:
    token, presentation_id = await auth_cookie_and_existing_presentation()
    cookies = {settings.session_cookie_name: token}
    timeout = httpx.Timeout(30.0, connect=5.0)
    async with httpx.AsyncClient(base_url=API_BASE, cookies=cookies, timeout=timeout) as client:
        session_data = payload(await client.get(f"/sessions/{QUICK_SESSION_ID}"))
        assistant_messages = [
            item
            for item in session_data.get("messages", [])
            if item.get("role") == "assistant" and item.get("solution_run_id")
        ]
        if not assistant_messages:
            raise AssertionError("prepared quick session has no completed solution message")
        solution_run_id = assistant_messages[-1]["solution_run_id"]
        run = payload(await client.get(f"/solution-runs/{solution_run_id}"))
        result = run.get("result") or {}
        print(f"QUICK_SHAPE run_keys={sorted(run)} result_keys={sorted(result)}")
        print(
            "QUICK_COUNTS "
            + " ".join(
                f"{key}={collection_size(value)}"
                for key, value in sorted(result.items())
            )
        )
        required = {
            "requirement_understanding",
            "historical_evidence",
            "capability_composition",
            "initial_recommendations",
            "pending_confirmations",
            "prerequisites_and_risks",
            "sources",
        }
        missing = sorted(key for key in required if not result.get(key))
        if run.get("status") != "completed" or missing:
            raise AssertionError(f"quick solution is incomplete; missing={missing}")
        print(f"QUICK_OK run={solution_run_id} sections={len(required)}")

        research = payload(await client.get(f"/research-tasks/{RESEARCH_TASK_ID}"))
        if research.get("status") != "completed" or not research.get("report"):
            raise AssertionError("prepared Deep Research task is not completed with a report")
        print(f"RESEARCH_OK task={RESEARCH_TASK_ID} progress={research.get('progress')}")

        latest = payload(
            await client.get(
                f"/research-tasks/{RESEARCH_TASK_ID}/interactive-presentations/latest"
            )
        )
        if latest is not None:
            if latest.get("research_task_id") != str(RESEARCH_TASK_ID):
                raise AssertionError("latest presentation belongs to a different research task")
            presentation_id = uuid.UUID(latest["id"])

        if presentation_id is None:
            accepted = payload(
                await client.post(
                    f"/research-tasks/{RESEARCH_TASK_ID}/interactive-presentations",
                    headers={"Idempotency-Key": f"recording-{uuid.uuid4()}"},
                    json={
                        "mode": "balanced",
                        "audience": "客户管理层与项目负责人",
                        "language": "zh-CN",
                        "visual_direction": "暖白咨询风、编辑部式商业叙事、克制的深蓝与橙色强调",
                    },
                )
            )
            presentation_id = uuid.UUID(accepted["presentation_id"])

        presentation = {}
        for _ in range(150):
            presentation = payload(await client.get(f"/presentations/{presentation_id}"))
            if presentation.get("status") in TERMINAL_PRESENTATION_STATUSES:
                break
            await asyncio.sleep(1)
        if presentation.get("status") != "ready":
            raise AssertionError(
                f"interactive HTML did not become ready: {presentation.get('status')} "
                f"{presentation.get('error_message') or ''}"
            )

        artifact = await client.get(f"/presentations/{presentation_id}/artifact")
        artifact.raise_for_status()
        html = artifact.text
        if len(html) < 5_000 or "东岳智行" not in html:
            raise AssertionError("interactive HTML artifact is missing expected research content")
        print(
            f"HTML_OK presentation={presentation_id} bytes={len(artifact.content)} "
            f"url={settings.public_base_url.rstrip('/')}/api/v1/presentations/"
            f"{presentation_id}/artifact"
        )


if __name__ == "__main__":
    asyncio.run(main())
