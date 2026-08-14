"""Verify built-in synthetic documents through the real index and retrieval path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.embedding import BailianEmbeddingProvider, BailianEmbeddingSettings  # noqa: E402
from app.ai.model_client import ModelClientError  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.database import configure_database  # noqa: E402
from app.db.models import (  # noqa: E402
    Capability,
    Experience,
    ReviewStatus,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
    User,
)
from app.services.asset_service import CapabilityService, ExperienceService  # noqa: E402
from app.services.retrieval_service import RetrievalService  # noqa: E402

DEFAULT_FIXTURE = Path("fixtures/builtin_documents/index_cases.json")
DEFAULT_REPORT = Path(".local/builtin-index-report.json")
BUILTIN_FIXTURE_WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-000000000099")


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _fixture_user(session, workspace_id: uuid.UUID) -> User:
    user = await session.scalar(
        select(User).where(
            User.workspace_id == workspace_id,
            User.feishu_user_id == "builtin-index-fixture",
        )
    )
    if user is None:
        user = User(
            workspace_id=workspace_id,
            feishu_user_id="builtin-index-fixture",
            name="内置索引验证员",
        )
        session.add(user)
        await session.flush()
    return user


async def _upsert_document(session, workspace_id, user, provider, item) -> None:
    source_url = f"builtin://index-fixture/{item['key']}"
    source = await session.scalar(
        select(Source).where(
            Source.workspace_id == workspace_id,
            Source.source_url == source_url,
            Source.is_deleted.is_(False),
        )
    )
    purpose = (
        SourcePurpose.EXPERIENCE
        if item["kind"] == "experience"
        else SourcePurpose.CAPABILITY
    )
    fingerprint = hashlib.sha256(item["content"].encode()).hexdigest()
    if source is None:
        source = Source(
            workspace_id=workspace_id,
            imported_by_id=user.id,
            type=SourceType.PASTED_TEXT,
            purpose=purpose,
            title=item["title"],
            content=item["content"],
            source_url=source_url,
            author="内置测试资料",
            status=SourceStatus.PENDING_REVIEW,
            freshness_status=SourceFreshness.CURRENT,
            permission_checked_at=datetime.now(UTC),
            content_fingerprint=fingerprint,
            content_version=1,
            is_demo=True,
        )
        session.add(source)
        await session.flush()
    else:
        if source.content_fingerprint != fingerprint:
            source.content_version += 1
        source.title = item["title"]
        source.content = item["content"]
        source.content_fingerprint = fingerprint
        source.status = SourceStatus.PENDING_REVIEW
        source.freshness_status = SourceFreshness.CURRENT
        source.permission_checked_at = datetime.now(UTC)

    model = Experience if item["kind"] == "experience" else Capability
    asset = await session.scalar(
        select(model).where(
            model.workspace_id == workspace_id,
            model.source_id == source.id,
            model.is_deleted.is_(False),
        )
    )
    if asset is None:
        asset = model(
            workspace_id=workspace_id,
            source_id=source.id,
            data=item["asset"],
            review_status=ReviewStatus.PENDING_REVIEW,
            embedding_ready=False,
        )
        session.add(asset)
        await session.flush()
    else:
        asset.data = item["asset"]
        asset.review_status = ReviewStatus.PENDING_REVIEW
        asset.embedding_ready = False
    service_type = ExperienceService if item["kind"] == "experience" else CapabilityService
    await service_type(session, workspace_id, user.id, provider).review(
        asset.id,
        "approve",
        "内置虚构文档：仅用于真实索引链路验收",
    )


async def run(fixture_path: Path, report_path: Path) -> int:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not settings.database_url:
        report = {
            "status": "skipped",
            "reason": "APP_DATABASE_URL is not configured",
            "fixture": str(fixture_path),
        }
        _write_report(report_path, report)
        return 0
    try:
        provider = BailianEmbeddingProvider(BailianEmbeddingSettings.from_env(Path(".env")))
    except ModelClientError as exc:
        report = {"status": "skipped", "reason": str(exc), "fixture": str(fixture_path)}
        _write_report(report_path, report)
        return 0

    _, factory = configure_database()
    workspace_id = BUILTIN_FIXTURE_WORKSPACE_ID
    if workspace_id == settings.demo_workspace_id:
        report = {
            "status": "failed",
            "reason": "built-in fixture workspace must be isolated from the business workspace",
            "fixture": str(fixture_path),
        }
        _write_report(report_path, report)
        return 1
    cases: list[dict] = []
    try:
        async with factory() as session:
            user = await _fixture_user(session, workspace_id)
            for item in fixture["documents"]:
                await _upsert_document(session, workspace_id, user, provider, item)
            for item in fixture["documents"]:
                result = await RetrievalService(session, workspace_id).retrieve(
                    item["query"], embedding_provider=provider
                )
                bucket = "experiences" if item["kind"] == "experience" else "capabilities"
                matches = result[bucket]
                top = matches[0] if matches else None
                cases.append(
                    {
                        "key": item["key"],
                        "query": item["query"],
                        "expected_title": item["expected_title"],
                        "top_title": top["source_title"] if top else None,
                        "passed": bool(top and top["source_title"] == item["expected_title"]),
                        "match_reasons": top["match_reasons"] if top else [],
                        "source_snapshot": top["source_snapshot"] if top else None,
                    }
                )
    except Exception as exc:
        report = {
            "status": "failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
            "fixture": str(fixture_path),
        }
        _write_report(report_path, report)
        return 1

    passed = all(item["passed"] for item in cases)
    report = {
        "status": "passed" if passed else "failed",
        "index_provider": provider.embedding_version,
        "workspace_id": str(workspace_id),
        "fixture_notice": fixture["notice"],
        "cases": cases,
    }
    _write_report(report_path, report)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    return asyncio.run(run(args.fixture, args.report))


if __name__ == "__main__":
    raise SystemExit(main())
