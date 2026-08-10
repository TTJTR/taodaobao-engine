import hashlib
import ipaddress
import socket
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    CustomerProfile,
    IntelligenceFreshness,
    IntelligenceItem,
    IntelligenceReviewStatus,
    IntelligenceSnapshot,
    ProfileIntelligenceProposal,
    ProfileStatus,
    ProposalStatus,
    SearchRun,
    SearchRunStatus,
)
from app.schemas.v2 import ManualIntelligenceSource


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


class IntelligenceService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

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
        await self.session.commit()
        await self.session.refresh(snapshot)
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
        proposal = await self._get(ProfileIntelligenceProposal, proposal_id)
        if proposal.profile_id != profile_id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "画像提案与客户不匹配", status_code=409)
        if proposal.status != ProposalStatus.PENDING_CONFIRMATION:
            raise AppError(ErrorCode.PROPOSAL_ALREADY_DECIDED, "该画像提案已处理", status_code=409)
        if accept:
            profile = await self._get(CustomerProfile, profile_id)
            # External intelligence is namespaced and cannot masquerade as confirmed internal fact.
            profile.profile = {
                **profile.profile,
                "external_intelligence": {
                    **profile.profile.get("external_intelligence", {}),
                    **proposal.proposed_patch,
                    "source_snapshot_id": str(proposal.snapshot_id),
                    "confirmed_by": str(self.user_id),
                },
            }
            profile.status = ProfileStatus.PENDING_CONFIRMATION
            profile.confirmed_by_id = None
            profile.confirmed_at = None
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
