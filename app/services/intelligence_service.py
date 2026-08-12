import hashlib
import ipaddress
import socket
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
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
    RawArtifactStatus,
    SearchRun,
    SearchRunStatus,
)
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
        normalized_content = " ".join(artifact.text_content.split())
        fingerprint = hashlib.sha256(
            f"{artifact.id}\n{artifact.content_sha256}\n{facts!r}".encode()
        ).hexdigest()
        existing = await self.session.scalar(
            select(IntelligenceItem).where(
                IntelligenceItem.workspace_id == self.workspace_id,
                IntelligenceItem.fingerprint == fingerprint,
                IntelligenceItem.is_deleted.is_(False),
            )
        )
        if existing is not None:
            item = existing
        else:
            item = IntelligenceItem(
                workspace_id=self.workspace_id,
                search_run_id=run.id,
                title=_ai_title(ai_output, artifact),
                source_url=artifact.source_url,
                source_domain=urlparse(artifact.source_url).hostname or "unknown",
                published_at=artifact.published_at,
                captured_at=artifact.captured_at,
                content=artifact.text_content,
                summary=_ai_summary(ai_output, normalized_content),
                facts=facts,
                fingerprint=fingerprint,
                freshness=IntelligenceFreshness.CURRENT,
                review_status=IntelligenceReviewStatus.PENDING,
                metadata_snapshot={
                    "provider": "ai_enrichment",
                    "raw_artifact_id": str(artifact.id),
                    "content_sha256": artifact.content_sha256,
                    "boundary": "external_public_information",
                },
            )
            self.session.add(item)
            await self.session.flush()
            self.session.add(
                IntelligenceItemArtifactLink(
                    workspace_id=self.workspace_id,
                    intelligence_item_id=item.id,
                    raw_artifact_id=artifact.id,
                    relation_type=ArtifactRelationType.PRIMARY,
                    source_snapshot={
                        "source_url": artifact.source_url,
                        "content_sha256": artifact.content_sha256,
                        "captured_at": artifact.captured_at.isoformat(),
                    },
                )
            )
        snapshot = await self._create_snapshot_uncommitted("customer_profile", [item])
        patch = _profile_diff(profile.profile, ai_output)
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
        await self.session.refresh(item)
        await self.session.refresh(snapshot)
        if proposal is not None:
            await self.session.refresh(proposal)
        return item, snapshot, proposal

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
        for source in sources:
            source_url = str(source.source_url)
            domain = validate_public_source_url(source_url)
            normalized = " ".join(source.content.split())
            fingerprint = hashlib.sha256(f"{source_url}\n{normalized}".encode()).hexdigest()
            exists = await self.session.scalar(
                select(IntelligenceItem.id).where(
                    IntelligenceItem.workspace_id == self.workspace_id,
                    IntelligenceItem.fingerprint == fingerprint,
                    IntelligenceItem.is_deleted.is_(False),
                )
            )
            if exists:
                duplicate += 1
                continue
            facts = _extract_facts(source.content)
            self.session.add(
                IntelligenceItem(
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
                        "captured_at": now.isoformat(),
                        "content_length": len(source.content),
                    },
                )
            )
            created += 1
        run.status = SearchRunStatus.COMPLETED if created else SearchRunStatus.PARTIAL
        run.result_summary = {"created": created, "duplicates": duplicate, "provider": "manual"}
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
        self, page: int, page_size: int, run_id: uuid.UUID | None
    ) -> tuple[list[IntelligenceItem], int]:
        filters = list(self._filters(IntelligenceItem))
        if run_id:
            filters.append(IntelligenceItem.search_run_id == run_id)
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

    async def get_item(self, item_id: uuid.UUID) -> IntelligenceItem:
        return await self._get(IntelligenceItem, item_id)

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
        self, profile_id: uuid.UUID, proposal_id: uuid.UUID, *, accept: bool, note: str | None
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
                        proposal.proposed_patch,
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
            "freshness": item.freshness.value,
            "review_status": item.review_status.value,
            "metadata_snapshot": item.metadata_snapshot,
        }
        if include_content:
            data["content"] = item.content
        return data
