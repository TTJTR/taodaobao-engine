import hashlib
import ipaddress
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.ai import AIEngine
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    ArtifactRelationType,
    CustomerProfile,
    CustomerProfileVersion,
    IntelligenceFreshness,
    IntelligenceItem,
    IntelligenceItemArtifactLink,
    IntelligenceReviewStatus,
    IntelligenceSnapshot,
    ProfileIntelligenceProposal,
    ProfileStatus,
    ProposalStatus,
    RawArtifact,
    RawArtifactKind,
    RawArtifactStatus,
    SearchRun,
    SearchRunStatus,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.schemas.intelligence_provider import EnrichmentJobRequest, EnrichmentJobResult
from app.schemas.v2 import ManualIntelligenceSource

MAX_AI_FACTS = 50
MAX_AI_FIELD_CHARS = 10_000


def validate_public_source_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError(
            ErrorCode.UNSAFE_EXTERNAL_URL, "仅允许公开的 HTTP/HTTPS 来源", status_code=422
        )
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise AppError(ErrorCode.UNSAFE_EXTERNAL_URL, "不允许本机或内网来源", status_code=422)
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        # Manual evidence may be captured while the source is temporarily unavailable.
        addresses = set()
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise AppError(
                ErrorCode.UNSAFE_EXTERNAL_URL, "不允许本机、内网或元数据地址", status_code=422
            )
    return hostname


def validate_provider_source_url(url: str) -> str:
    hostname = validate_public_source_url(url)
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise AppError(
            ErrorCode.UNSAFE_EXTERNAL_URL,
            "Provider 来源域名当前无法验证",
            status_code=422,
        ) from exc
    if not addresses:
        raise AppError(
            ErrorCode.UNSAFE_EXTERNAL_URL,
            "Provider 来源域名没有可验证地址",
            status_code=422,
        )
    return hostname


def _extract_facts(content: str) -> list[dict[str, str]]:
    paragraphs = [part.strip() for part in content.replace("\r", "\n").split("\n") if part.strip()]
    return [
        {"text": paragraph[:1000], "classification": "external_public_information"}
        for paragraph in paragraphs[:20]
    ]


def _normalize_ai_facts(ai_output: dict, artifact: RawArtifact) -> list[dict]:
    facts: list[dict] = []
    for field, value in sorted(ai_output.items()):
        if field in {"source_ids", "title", "summary"} or value in (None, "", [], {}):
            continue
        safe_value = _bounded_json_value(value)
        facts.append(
            {
                "field": str(field)[:128],
                "value": safe_value,
                "classification": "external_public_information",
                "raw_artifact_id": str(artifact.id),
                "content_sha256": artifact.content_sha256,
            }
        )
        if len(facts) >= MAX_AI_FACTS:
            break
    if not facts:
        raise AppError(ErrorCode.AI_OUTPUT_INVALID, "AI 未返回可用情报事实", status_code=422)
    return facts


def _profile_diff(profile_data: dict, ai_output: dict) -> dict:
    current = profile_data.get("external_intelligence", {})
    patch: dict[str, dict] = {}
    for field, proposed in sorted(ai_output.items()):
        if field in {"source_ids", "title", "summary"} or proposed in (None, "", [], {}):
            continue
        proposed = _bounded_json_value(proposed)
        previous = current.get(field)
        if previous != proposed:
            patch[field] = {
                "operation": "replace" if field in current else "add",
                "previous": previous,
                "proposed": proposed,
            }
    return patch


def _normalize_governance_value(value) -> str:
    bounded = _bounded_json_value(value)
    if isinstance(bounded, str):
        normalized = " ".join(bounded.split()).casefold()
        return json.dumps(normalized, ensure_ascii=False)
    if isinstance(bounded, list):
        normalized = sorted(
            (_normalize_governance_value(item) for item in bounded),
            key=str.casefold,
        )
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reinforced_confidence(current: float, incoming: float) -> float:
    current = min(1.0, max(0.0, current))
    incoming = min(1.0, max(0.0, incoming))
    return round(1 - (1 - current) * (1 - incoming), 6)


def _conflict_patch(items: list[IntelligenceItem]) -> dict:
    items = sorted(items, key=lambda item: (item.created_at, str(item.id)))
    group_id = next((item.conflict_group_id for item in items if item.conflict_group_id), None)
    return {
        "resolution_required": True,
        "conflict_group_id": str(group_id) if group_id else None,
        "candidates": [
            {
                "intelligence_item_id": str(item.id),
                "value": item.facts[0].get("value") if item.facts else None,
                "confidence": item.metadata_snapshot.get("confidence"),
                "latest_captured_at": item.metadata_snapshot.get("latest_captured_at"),
            }
            for item in items
        ],
    }


def _apply_profile_patch(current: dict, patch: dict) -> dict:
    updated = dict(current)
    for field, change in patch.items():
        if isinstance(change, dict) and "operation" in change:
            if change.get("operation") not in {"add", "replace"}:
                raise AppError(ErrorCode.VALIDATION_FAILED, "画像提议格式无效", status_code=422)
            updated[field] = change.get("proposed")
        else:
            updated[field] = _bounded_json_value(change)
    return updated


def _bounded_json_value(value):
    if isinstance(value, str):
        return value[:MAX_AI_FIELD_CHARS]
    if isinstance(value, list):
        return [_bounded_json_value(item) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded_json_value(item)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_AI_FIELD_CHARS]


def _ai_title(ai_output: dict, artifact: RawArtifact) -> str:
    title = ai_output.get("title") or artifact.metadata_snapshot.get("title") or artifact.source_url
    return str(title)[:500]


def _ai_summary(ai_output: dict, fallback: str) -> str:
    summary = ai_output.get("summary") or ai_output.get("background") or fallback
    return str(summary)[:2000]


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path.rstrip("/") or "/",
        fragment="",
    ).geturl()


def _verify_provider_facts(
    request: EnrichmentJobRequest,
    result: EnrichmentJobResult,
    artifacts: list[RawArtifact],
) -> tuple[list[dict], dict[uuid.UUID, RawArtifact], dict[str, object]]:
    allowed_fields = set(request.allowed_fields)
    artifacts_by_url = {
        _canonical_url(source_url): artifact
        for artifact in artifacts
        if (source_url := artifact.normalized_url or artifact.source_url)
        and artifact.text_content
    }
    verified_facts: list[dict] = []
    linked_artifacts: dict[uuid.UUID, RawArtifact] = {}
    proposed_values: dict[str, object] = {}
    for fact in result.facts:
        if fact.field not in allowed_fields:
            raise AppError(
                ErrorCode.AI_OUTPUT_INVALID,
                "Provider 返回了未授权字段",
                status_code=422,
                details={"field": fact.field},
            )
        citations = []
        for citation in fact.citations:
            source_url = str(citation.url)
            validate_provider_source_url(source_url)
            artifact = artifacts_by_url.get(_canonical_url(source_url))
            if artifact is None:
                raise AppError(
                    ErrorCode.AI_OUTPUT_INVALID,
                    "Provider 引文没有对应的原始制品",
                    status_code=422,
                )
            quote = citation.quote.strip()
            if not quote or quote not in (artifact.text_content or ""):
                raise AppError(
                    ErrorCode.AI_OUTPUT_INVALID,
                    "Provider 引文无法在原始正文中定位",
                    status_code=422,
                )
            quote_hash = hashlib.sha256(quote.encode()).hexdigest()
            citations.append(
                {
                    "raw_artifact_id": str(artifact.id),
                    "source_url": artifact.source_url,
                    "quote": quote,
                    "quote_hash": quote_hash,
                    "provider_confidence": citation.provider_confidence,
                    "captured_at": artifact.captured_at.isoformat(),
                }
            )
            linked_artifacts[artifact.id] = artifact
        fact_artifact_ids = list(
            dict.fromkeys(citation["raw_artifact_id"] for citation in citations)
        )
        verified_facts.append(
            {
                "field": fact.field,
                "value": _bounded_json_value(fact.value),
                "category": fact.category,
                "classification": "external_public_information",
                "provider_confidence": fact.provider_confidence,
                "citation_status": "verified",
                "citations": citations,
                "raw_artifact_ids": fact_artifact_ids,
            }
        )
        proposed_values[fact.field] = _bounded_json_value(fact.value)
    if not verified_facts or not linked_artifacts:
        raise AppError(
            ErrorCode.AI_OUTPUT_INVALID,
            "Provider 未返回可验证事实",
            status_code=422,
        )
    return verified_facts, linked_artifacts, proposed_values


class IntelligenceService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

    async def enrich_artifact_for_profile(
        self,
        artifact_id: uuid.UUID,
        profile_id: uuid.UUID,
        ai_engine: AIEngine,
    ) -> tuple[IntelligenceItem, IntelligenceSnapshot, ProfileIntelligenceProposal | None]:
        artifact = await self._get(RawArtifact, artifact_id)
        profile = await self._get(CustomerProfile, profile_id)
        if artifact.status not in {RawArtifactStatus.CAPTURED, RawArtifactStatus.PARTIAL}:
            raise AppError(
                ErrorCode.INTELLIGENCE_ITEM_UNAVAILABLE,
                "原始情报制品当前不可用于抽取",
                status_code=409,
            )
        if not artifact.text_content or not artifact.source_url or not artifact.search_run_id:
            raise AppError(
                ErrorCode.INTELLIGENCE_ITEM_UNAVAILABLE,
                "原始情报缺少正文、来源 URL 或采集运行",
                status_code=409,
            )
        run = await self._get(SearchRun, artifact.search_run_id)
        ai_output = await ai_engine.extract_profile(
            artifact.text_content,
            [str(artifact.id)],
        )
        facts = _normalize_ai_facts(ai_output, artifact)
        items: list[IntelligenceItem] = []
        conflicts: dict[str, dict] = {}
        for raw_fact in facts:
            fact = {
                "field": raw_fact["field"],
                "value": raw_fact["value"],
                "category": "ai_profile_enrichment",
                "classification": raw_fact["classification"],
                "provider_confidence": 0.5,
                "citation_status": "artifact_bound",
                "citations": [
                    {
                        "raw_artifact_id": str(artifact.id),
                        "source_url": artifact.source_url,
                        "quote": artifact.text_content[:MAX_AI_FIELD_CHARS],
                        "quote_hash": hashlib.sha256(artifact.text_content.encode()).hexdigest(),
                        "provider_confidence": None,
                        "captured_at": artifact.captured_at.isoformat(),
                    }
                ],
            }
            item, conflict = await self._govern_fact(
                run=run,
                profile=profile,
                company_name=profile.customer_name,
                provider="ai_enrichment",
                provider_job_id=None,
                provider_status="completed",
                tool_calls_used=None,
                cost_usd=None,
                fact=fact,
                linked_artifacts={artifact.id: artifact},
            )
            if item not in items:
                items.append(item)
            if conflict:
                conflicts[fact["field"]] = conflict
        snapshot = await self._create_snapshot_uncommitted("customer_profile", items)
        patch = _profile_diff(profile.profile, ai_output)
        for field, conflict in conflicts.items():
            if field in patch:
                patch[field] = {**patch[field], **conflict}
        proposal = None
        if patch:
            proposal = ProfileIntelligenceProposal(
                workspace_id=self.workspace_id,
                profile_id=profile.id,
                snapshot_id=snapshot.id,
                created_by_id=self.user_id,
                proposed_patch=patch,
                status=ProposalStatus.PENDING_CONFIRMATION,
            )
            self.session.add(proposal)
        await self.session.commit()
        for item in items:
            await self.session.refresh(item)
        await self.session.refresh(snapshot)
        if proposal is not None:
            await self.session.refresh(proposal)
        return items[0], snapshot, proposal

    async def accept_provider_result(
        self,
        *,
        run_id: uuid.UUID,
        profile_id: uuid.UUID,
        request: EnrichmentJobRequest,
        result: EnrichmentJobResult,
    ) -> tuple[IntelligenceItem, IntelligenceSnapshot, ProfileIntelligenceProposal | None]:
        run = await self._get(SearchRun, run_id)
        profile = await self._get(CustomerProfile, profile_id)
        artifacts = list(
            await self.session.scalars(
                select(RawArtifact).where(
                    RawArtifact.workspace_id == self.workspace_id,
                    RawArtifact.search_run_id == run.id,
                    RawArtifact.status.in_([RawArtifactStatus.CAPTURED, RawArtifactStatus.PARTIAL]),
                    RawArtifact.is_deleted.is_(False),
                )
            )
        )
        verified_facts, linked_artifacts, proposed_values = _verify_provider_facts(
            request, result, artifacts
        )
        items: list[IntelligenceItem] = []
        conflicts: dict[str, dict] = {}
        for fact in verified_facts:
            fact_artifacts = {
                uuid.UUID(artifact_id): linked_artifacts[uuid.UUID(artifact_id)]
                for artifact_id in fact["raw_artifact_ids"]
            }
            item, conflict = await self._govern_fact(
                run=run,
                profile=profile,
                company_name=request.company_name,
                provider="open_enrich",
                provider_job_id=result.provider_job_id,
                provider_status=result.status,
                tool_calls_used=result.tool_calls_used,
                cost_usd=result.cost_usd,
                fact=fact,
                linked_artifacts=fact_artifacts,
            )
            if item not in items:
                items.append(item)
            if conflict:
                conflicts[fact["field"]] = conflict
        snapshot = await self._create_snapshot_uncommitted("customer_profile", items)
        patch = _profile_diff(profile.profile, proposed_values)
        for field, conflict in conflicts.items():
            if field in patch:
                patch[field] = {**patch[field], **conflict}
        proposal = None
        if patch:
            proposal = ProfileIntelligenceProposal(
                workspace_id=self.workspace_id,
                profile_id=profile.id,
                snapshot_id=snapshot.id,
                created_by_id=self.user_id,
                proposed_patch=patch,
                status=ProposalStatus.PENDING_CONFIRMATION,
            )
            self.session.add(proposal)
        await self.session.commit()
        for item in items:
            await self.session.refresh(item)
        await self.session.refresh(snapshot)
        if proposal:
            await self.session.refresh(proposal)
        return items[0], snapshot, proposal

    async def _govern_fact(
        self,
        *,
        run: SearchRun,
        profile: CustomerProfile,
        company_name: str,
        provider: str,
        provider_job_id: str | None,
        provider_status: str,
        tool_calls_used: int | None,
        cost_usd: float | None,
        fact: dict,
        linked_artifacts: dict[uuid.UUID, RawArtifact],
    ) -> tuple[IntelligenceItem, dict | None]:
        field_name = str(fact["field"])
        normalized_value = _normalize_governance_value(fact["value"])
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {
                "key": (
                    f"{self.workspace_id}:intelligence-governance:"
                    f"{profile.id}:{field_name}"
                )
            },
        )
        candidates = list(
            await self.session.scalars(
                select(IntelligenceItem)
                .where(
                    IntelligenceItem.workspace_id == self.workspace_id,
                    IntelligenceItem.is_deleted.is_(False),
                    IntelligenceItem.metadata_snapshot["profile_id"].astext == str(profile.id),
                    IntelligenceItem.metadata_snapshot["field_name"].astext == field_name,
                )
                .with_for_update()
            )
        )
        exact = next(
            (
                row
                for row in candidates
                if row.metadata_snapshot.get("normalized_value") == normalized_value
            ),
            None,
        )
        captured_at = max(row.captured_at for row in linked_artifacts.values())
        if exact is not None:
            await self._link_artifacts(exact, linked_artifacts, corroborating=True)
            exact.captured_at = max(exact.captured_at, captured_at)
            metadata = dict(exact.metadata_snapshot)
            metadata["latest_captured_at"] = exact.captured_at.isoformat()
            metadata["confidence"] = _reinforced_confidence(
                float(metadata.get("confidence", 0)),
                float(fact["provider_confidence"]),
            )
            metadata["source_count"] = await self._artifact_link_count(exact.id)
            exact.metadata_snapshot = metadata
            group = [row for row in candidates if row.conflict_group_id == exact.conflict_group_id]
            return exact, _conflict_patch(group) if exact.conflict_group_id else None

        primary = next(iter(linked_artifacts.values()))
        fingerprint = hashlib.sha256(
            f"{profile.id}\n{field_name}\n{normalized_value}".encode()
        ).hexdigest()
        item = IntelligenceItem(
            workspace_id=self.workspace_id,
            search_run_id=run.id,
            title=f"{company_name} - {field_name}",
            source_url=primary.source_url or "unknown",
            source_domain=urlparse(primary.source_url or "").hostname or "unknown",
            published_at=primary.published_at,
            captured_at=captured_at,
            content="\n\n".join(citation["quote"] for citation in fact["citations"]),
            summary=f"{provider} candidate fact: {field_name}",
            facts=[fact],
            fingerprint=fingerprint,
            freshness=IntelligenceFreshness.CURRENT,
            review_status=IntelligenceReviewStatus.PENDING,
            metadata_snapshot={
                "provider": provider,
                "provider_job_id": provider_job_id,
                "provider_status": provider_status,
                "profile_id": str(profile.id),
                "field_name": field_name,
                "normalized_value": normalized_value,
                "latest_captured_at": captured_at.isoformat(),
                "confidence": fact["provider_confidence"],
                "source_count": len(linked_artifacts),
                "tool_calls_used": tool_calls_used,
                "cost_usd": cost_usd,
                "boundary": "external_public_information",
            },
        )
        if candidates:
            group_id = next(
                (row.conflict_group_id for row in candidates if row.conflict_group_id),
                uuid.uuid4(),
            )
            item.conflict_group_id = group_id
            for row in candidates:
                row.conflict_group_id = group_id
        self.session.add(item)
        await self.session.flush()
        await self._link_artifacts(item, linked_artifacts, corroborating=False)
        return item, _conflict_patch([*candidates, item]) if candidates else None

    async def _link_artifacts(
        self,
        item: IntelligenceItem,
        artifacts: dict[uuid.UUID, RawArtifact],
        *,
        corroborating: bool,
    ) -> None:
        existing_ids = set(
            await self.session.scalars(
                select(IntelligenceItemArtifactLink.raw_artifact_id).where(
                    IntelligenceItemArtifactLink.workspace_id == self.workspace_id,
                    IntelligenceItemArtifactLink.intelligence_item_id == item.id,
                    IntelligenceItemArtifactLink.is_deleted.is_(False),
                )
            )
        )
        for index, artifact in enumerate(artifacts.values()):
            if artifact.id in existing_ids:
                continue
            self.session.add(
                IntelligenceItemArtifactLink(
                    workspace_id=self.workspace_id,
                    intelligence_item_id=item.id,
                    raw_artifact_id=artifact.id,
                    relation_type=(
                        ArtifactRelationType.CORROBORATING
                        if corroborating or index > 0
                        else ArtifactRelationType.PRIMARY
                    ),
                    source_snapshot={
                        "source_url": artifact.source_url,
                        "content_sha256": artifact.content_sha256,
                        "captured_at": artifact.captured_at.isoformat(),
                    },
                )
            )
        await self.session.flush()

    async def _artifact_link_count(self, item_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(IntelligenceItemArtifactLink)
                .where(
                    IntelligenceItemArtifactLink.workspace_id == self.workspace_id,
                    IntelligenceItemArtifactLink.intelligence_item_id == item_id,
                    IntelligenceItemArtifactLink.is_deleted.is_(False),
                )
            )
            or 0
        )

    async def queue_provider_enrichment(
        self,
        run_id: uuid.UUID,
        profile_id: uuid.UUID,
        request: EnrichmentJobRequest,
    ) -> WorkflowTask:
        run = await self._get(SearchRun, run_id)
        await self._get(CustomerProfile, profile_id)
        if request.client_job_id != run.id:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "Provider client_job_id 必须等于 SearchRun ID",
                status_code=422,
            )
        if request.website_url is not None:
            validate_provider_source_url(str(request.website_url))
        task = await self.session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "open_enrich",
                WorkflowTask.target_id == run.id,
                *self._filters(WorkflowTask),
            )
        )
        payload = {
            "profile_id": str(profile_id),
            "user_id": str(self.user_id),
            "request": request.model_dump(mode="json"),
            "provider_job_id": None,
            "poll_count": 0,
            "cost_usd": 0,
            "tool_calls_used": 0,
        }
        if task is None:
            task = WorkflowTask(
                workspace_id=self.workspace_id,
                kind="open_enrich",
                target_id=run.id,
                status=WorkflowTaskStatus.QUEUED,
                stage="queued",
                trace_id=run.trace_id,
                payload=payload,
                attempt_count=0,
                max_attempts=200,
                available_at=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            self.session.add(task)
        elif task.payload.get("request") != payload["request"]:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "该 SearchRun 已存在不同参数的富化任务",
                status_code=409,
            )
        run.provider = "open_enrich"
        run.status = SearchRunStatus.QUEUED
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def create_run(
        self,
        *,
        query: str,
        purpose: str,
        provider: str,
        sources: list[ManualIntelligenceSource],
    ) -> SearchRun:
        now = datetime.now(UTC)
        run = SearchRun(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            query=query.strip(),
            purpose=purpose,
            provider=provider,
            status=SearchRunStatus.RUNNING,
            trace_id=str(uuid.uuid4()),
            input_snapshot={"query": query.strip(), "source_count": len(sources)},
            result_summary={},
        )
        self.session.add(run)
        await self.session.flush()
        created = 0
        duplicate = 0
        artifacts_created = 0
        artifacts_reused = 0
        for source in sources:
            source_url = str(source.source_url)
            domain = validate_public_source_url(source_url)
            normalized = " ".join(source.content.split())
            fingerprint = hashlib.sha256(f"{source_url}\n{normalized}".encode()).hexdigest()
            artifact = await self.session.scalar(
                select(RawArtifact).where(
                    RawArtifact.workspace_id == self.workspace_id,
                    RawArtifact.artifact_key == fingerprint,
                    RawArtifact.is_deleted.is_(False),
                )
            )
            if artifact is None:
                artifact = RawArtifact(
                    workspace_id=self.workspace_id,
                    search_run_id=run.id,
                    artifact_key=fingerprint,
                    kind=RawArtifactKind.PASTED_TEXT,
                    status=RawArtifactStatus.CAPTURED,
                    provider="manual",
                    source_url=source_url,
                    normalized_url=source_url,
                    mime_type="text/plain",
                    content_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
                    byte_size=len(source.content.encode()),
                    text_content=source.content,
                    published_at=source.published_at,
                    captured_at=now,
                    security_report={"public_url_validated": True},
                    metadata_snapshot={"title": source.title.strip(), "input_mode": "manual"},
                )
                self.session.add(artifact)
                await self.session.flush()
                artifacts_created += 1
            else:
                artifacts_reused += 1
            item = await self.session.scalar(
                select(IntelligenceItem).where(
                    IntelligenceItem.workspace_id == self.workspace_id,
                    IntelligenceItem.fingerprint == fingerprint,
                    IntelligenceItem.is_deleted.is_(False),
                )
            )
            if item is not None:
                await self._link_artifacts(item, {artifact.id: artifact}, corroborating=True)
                duplicate += 1
                continue
            facts = _extract_facts(source.content)
            item = IntelligenceItem(
                workspace_id=self.workspace_id,
                search_run_id=run.id,
                title=source.title.strip(),
                source_url=source_url,
                source_domain=domain,
                published_at=source.published_at,
                captured_at=now,
                content=source.content,
                summary=(facts[0]["text"] if facts else source.content[:1000]),
                facts=facts,
                fingerprint=fingerprint,
                freshness=IntelligenceFreshness.CURRENT,
                review_status=IntelligenceReviewStatus.PENDING,
                metadata_snapshot={
                    "provider": "manual",
                    "raw_artifact_id": str(artifact.id),
                    "content_sha256": artifact.content_sha256,
                    "captured_at": now.isoformat(),
                    "content_length": len(source.content),
                    "boundary": "external_public_information",
                },
            )
            self.session.add(item)
            await self.session.flush()
            await self._link_artifacts(item, {artifact.id: artifact}, corroborating=False)
            created += 1
        run.status = SearchRunStatus.COMPLETED if created else SearchRunStatus.PARTIAL
        run.result_summary = {
            "created": created,
            "duplicates": duplicate,
            "artifacts_created": artifacts_created,
            "artifacts_reused": artifacts_reused,
            "provider": "manual",
        }
        run.completed_at = now
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: uuid.UUID) -> SearchRun:
        entity = await self._get(SearchRun, run_id)
        return entity

    async def list_runs(self, page: int, page_size: int) -> tuple[list[SearchRun], int]:
        filters = self._filters(SearchRun)
        result = await self.session.scalars(
            select(SearchRun)
            .where(*filters)
            .order_by(SearchRun.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(SearchRun).where(*filters)
        )
        return list(result), int(total or 0)

    async def list_items(
        self,
        page: int,
        page_size: int,
        run_id: uuid.UUID | None,
        *,
        freshness: IntelligenceFreshness | None = None,
        conflict_group_id: uuid.UUID | None = None,
    ) -> tuple[list[IntelligenceItem], int]:
        filters = list(self._filters(IntelligenceItem))
        if run_id:
            filters.append(IntelligenceItem.search_run_id == run_id)
        if freshness is not None:
            filters.append(IntelligenceItem.freshness == freshness)
        if conflict_group_id is not None:
            filters.append(IntelligenceItem.conflict_group_id == conflict_group_id)
        result = await self.session.scalars(
            select(IntelligenceItem)
            .where(*filters)
            .order_by(IntelligenceItem.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(IntelligenceItem).where(*filters)
        )
        return list(result), int(total or 0)

    async def list_artifacts(
        self, page: int, page_size: int, run_id: uuid.UUID | None
    ) -> tuple[list[RawArtifact], int]:
        filters = list(self._filters(RawArtifact))
        if run_id is not None:
            filters.append(RawArtifact.search_run_id == run_id)
        result = await self.session.scalars(
            select(RawArtifact)
            .where(*filters)
            .order_by(RawArtifact.captured_at.desc(), RawArtifact.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(RawArtifact).where(*filters)
        )
        return list(result), int(total or 0)

    async def get_artifact(self, artifact_id: uuid.UUID) -> RawArtifact:
        return await self._get(RawArtifact, artifact_id)

    async def get_item(self, item_id: uuid.UUID) -> IntelligenceItem:
        return await self._get(IntelligenceItem, item_id)

    async def reassess_freshness(self, *, stale_after_days: int = 90) -> dict:
        assessed_at = datetime.now(UTC)
        stale_before = assessed_at - timedelta(days=stale_after_days)
        items = list(
            await self.session.scalars(
                select(IntelligenceItem).where(
                    IntelligenceItem.workspace_id == self.workspace_id,
                    IntelligenceItem.is_deleted.is_(False),
                    IntelligenceItem.freshness == IntelligenceFreshness.CURRENT,
                )
            )
        )
        marked_stale = 0
        for item in items:
            if item.captured_at >= stale_before:
                continue
            metadata = dict(item.metadata_snapshot)
            metadata["freshness_assessed_at"] = assessed_at.isoformat()
            metadata["freshness_reason"] = "captured_at_exceeded_stale_threshold"
            metadata["stale_after_days"] = stale_after_days
            item.metadata_snapshot = metadata
            item.freshness = IntelligenceFreshness.STALE
            marked_stale += 1
        await self.session.commit()
        return {
            "assessed_at": assessed_at.isoformat(),
            "stale_after_days": stale_after_days,
            "scanned": len(items),
            "marked_stale": marked_stale,
        }

    async def create_snapshot(
        self, purpose: str, item_ids: list[uuid.UUID]
    ) -> IntelligenceSnapshot:
        unique_ids = list(dict.fromkeys(item_ids))
        items = list(
            await self.session.scalars(
                select(IntelligenceItem).where(
                    IntelligenceItem.workspace_id == self.workspace_id,
                    IntelligenceItem.id.in_(unique_ids),
                    IntelligenceItem.is_deleted.is_(False),
                    IntelligenceItem.freshness == IntelligenceFreshness.CURRENT,
                )
            )
        )
        if len(items) != len(unique_ids):
            raise AppError(
                ErrorCode.INTELLIGENCE_ITEM_UNAVAILABLE,
                "部分情报不存在、已失效或不属于当前工作区",
                status_code=409,
            )
        snapshot = await self._create_snapshot_uncommitted(purpose, items)
        await self.session.commit()
        await self.session.refresh(snapshot)
        return snapshot

    async def _create_snapshot_uncommitted(
        self, purpose: str, items: list[IntelligenceItem]
    ) -> IntelligenceSnapshot:
        rows = [
            self.serialize_item(item, include_content=True)
            for item in sorted(items, key=lambda x: str(x.id))
        ]
        payload = {"purpose": purpose, "captured_at": datetime.now(UTC).isoformat(), "items": rows}
        fingerprint = hashlib.sha256(repr(payload).encode()).hexdigest()
        existing = await self.session.scalar(
            select(IntelligenceSnapshot).where(
                IntelligenceSnapshot.workspace_id == self.workspace_id,
                IntelligenceSnapshot.fingerprint == fingerprint,
                IntelligenceSnapshot.is_deleted.is_(False),
            )
        )
        if existing:
            return existing
        snapshot = IntelligenceSnapshot(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            purpose=purpose,
            item_ids=[str(item.id) for item in items],
            snapshot_data=payload,
            fingerprint=fingerprint,
            schema_version="v2.0",
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def get_snapshot(self, snapshot_id: uuid.UUID) -> IntelligenceSnapshot:
        return await self._get(IntelligenceSnapshot, snapshot_id)

    async def create_profile_proposal(
        self, profile_id: uuid.UUID, snapshot_id: uuid.UUID, patch: dict
    ) -> ProfileIntelligenceProposal:
        await self._get(CustomerProfile, profile_id)
        await self._get(IntelligenceSnapshot, snapshot_id)
        proposal = ProfileIntelligenceProposal(
            workspace_id=self.workspace_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
            created_by_id=self.user_id,
            proposed_patch=patch,
            status=ProposalStatus.PENDING_CONFIRMATION,
        )
        self.session.add(proposal)
        await self.session.commit()
        await self.session.refresh(proposal)
        return proposal

    async def decide_proposal(
        self,
        profile_id: uuid.UUID,
        proposal_id: uuid.UUID,
        *,
        accept: bool,
        note: str | None,
        selected_candidates: dict[str, uuid.UUID] | None = None,
    ) -> ProfileIntelligenceProposal:
        proposal = await self.session.scalar(
            select(ProfileIntelligenceProposal)
            .where(
                ProfileIntelligenceProposal.id == proposal_id,
                *self._filters(ProfileIntelligenceProposal),
            )
            .with_for_update()
        )
        if proposal is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        if proposal.profile_id != profile_id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "画像提案与客户不匹配", status_code=409)
        if proposal.status != ProposalStatus.PENDING_CONFIRMATION:
            raise AppError(ErrorCode.PROPOSAL_ALREADY_DECIDED, "该画像提案已处理", status_code=409)
        if accept:
            resolved_patch = self._resolve_conflict_candidates(
                proposal.proposed_patch, selected_candidates or {}
            )
            proposal.proposed_patch = resolved_patch
            profile = await self.session.scalar(
                select(CustomerProfile)
                .where(
                    CustomerProfile.id == profile_id,
                    *self._filters(CustomerProfile),
                )
                .with_for_update()
            )
            if profile is None:
                raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
            self.session.add(
                CustomerProfileVersion(
                    workspace_id=self.workspace_id,
                    profile_id=profile.id,
                    proposal_id=proposal.id,
                    version=profile.version,
                    changed_by_id=self.user_id,
                    change_type="intelligence_confirm",
                    profile_snapshot={
                        "customer_name": profile.customer_name,
                        "profile": profile.profile,
                        "status": profile.status.value,
                        "confirmed_by_id": str(profile.confirmed_by_id)
                        if profile.confirmed_by_id
                        else None,
                        "confirmed_at": profile.confirmed_at.isoformat()
                        if profile.confirmed_at
                        else None,
                    },
                )
            )
            # External intelligence is namespaced and cannot masquerade as confirmed internal fact.
            profile.profile = {
                **profile.profile,
                "external_intelligence": {
                    **profile.profile.get("external_intelligence", {}),
                    **_apply_profile_patch(
                        profile.profile.get("external_intelligence", {}),
                        resolved_patch,
                    ),
                    "source_snapshot_id": str(proposal.snapshot_id),
                    "confirmed_by": str(self.user_id),
                },
            }
            profile.status = ProfileStatus.PENDING_CONFIRMATION
            profile.confirmed_by_id = None
            profile.confirmed_at = None
            profile.version += 1
            proposal.status = ProposalStatus.ACCEPTED
        else:
            proposal.status = ProposalStatus.REJECTED
        proposal.decided_by_id = self.user_id
        proposal.decided_at = datetime.now(UTC)
        proposal.decision_note = note
        await self.session.commit()
        await self.session.refresh(proposal)
        return proposal

    @staticmethod
    def _resolve_conflict_candidates(
        proposed_patch: dict, selected_candidates: dict[str, uuid.UUID]
    ) -> dict:
        conflict_fields = {
            field
            for field, change in proposed_patch.items()
            if isinstance(change, dict) and change.get("resolution_required") is True
        }
        unexpected = set(selected_candidates) - conflict_fields
        if unexpected:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "候选选择包含非冲突字段",
                status_code=422,
                details={"fields": sorted(unexpected)},
            )
        resolved = dict(proposed_patch)
        for field in sorted(conflict_fields):
            selected_id = selected_candidates.get(field)
            if selected_id is None:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    "冲突画像提议必须先显式选择候选值",
                    status_code=409,
                    details={"field": field},
                )
            change = dict(proposed_patch[field])
            candidate = next(
                (
                    row
                    for row in change.get("candidates", [])
                    if row.get("intelligence_item_id") == str(selected_id)
                ),
                None,
            )
            if candidate is None:
                raise AppError(
                    ErrorCode.VALIDATION_FAILED,
                    "所选情报候选不属于当前冲突字段",
                    status_code=422,
                    details={"field": field, "intelligence_item_id": str(selected_id)},
                )
            change["proposed"] = candidate.get("value")
            change["resolution_required"] = False
            change["selected_candidate_id"] = str(selected_id)
            resolved[field] = change
        return resolved

    def _filters(self, model) -> tuple:
        return model.workspace_id == self.workspace_id, model.is_deleted.is_(False)

    async def _get(self, model, entity_id: uuid.UUID):
        entity = await self.session.scalar(
            select(model).where(model.id == entity_id, *self._filters(model))
        )
        if entity is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        return entity

    @staticmethod
    def serialize_item(item: IntelligenceItem, *, include_content: bool = False) -> dict:
        data = {
            "id": str(item.id),
            "search_run_id": str(item.search_run_id),
            "title": item.title,
            "source_url": item.source_url,
            "source_domain": item.source_domain,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "captured_at": item.captured_at.isoformat(),
            "summary": item.summary,
            "facts": item.facts,
            "fingerprint": item.fingerprint,
            "conflict_group_id": (
                str(item.conflict_group_id) if item.conflict_group_id else None
            ),
            "freshness": item.freshness.value,
            "review_status": item.review_status.value,
            "metadata_snapshot": item.metadata_snapshot,
        }
        if include_content:
            data["content"] = item.content
        return data

    @staticmethod
    def serialize_artifact(artifact: RawArtifact, *, include_text: bool = False) -> dict:
        data = {
            "id": str(artifact.id),
            "search_run_id": str(artifact.search_run_id) if artifact.search_run_id else None,
            "kind": artifact.kind.value,
            "status": artifact.status.value,
            "provider": artifact.provider,
            "source_url": artifact.source_url,
            "normalized_url": artifact.normalized_url,
            "source_filename": artifact.source_filename,
            "mime_type": artifact.mime_type,
            "http_status": artifact.http_status,
            "content_sha256": artifact.content_sha256,
            "byte_size": artifact.byte_size,
            "published_at": artifact.published_at.isoformat() if artifact.published_at else None,
            "captured_at": artifact.captured_at.isoformat(),
            "error_code": artifact.error_code,
            "error_summary": artifact.error_summary,
        }
        if include_text:
            data["text_content"] = artifact.text_content
        return data
