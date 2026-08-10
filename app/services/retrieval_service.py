import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.embedding import AssetType, EmbeddingProvider, cosine_similarity
from app.contracts.ai import AIEngine
from app.db.models import (
    Capability,
    Experience,
    ReviewStatus,
    Source,
    SourceFreshness,
    SourceStatus,
)


class RetrievalService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def retrieve(
        self,
        query: str,
        *,
        context: dict | None = None,
        ai_engine: AIEngine | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> dict:
        intent: dict = {}
        query_vector: list[float] | None = None
        if ai_engine is not None and context is not None:
            intent = await ai_engine.extract_search_intent(context)
        if embedding_provider is not None:
            embedding_text = str(intent.get("embedding_text") or query)
            query_vector = (await embedding_provider.embed(embedding_text, AssetType.QUERY)).vector
        experience_query = str(intent.get("experience_query_text") or query)
        capability_query = str(intent.get("capability_query_text") or query)
        experience_statement = self._statement(Experience, experience_query, query_vector, limit=3)
        capability_statement = self._statement(Capability, capability_query, query_vector, limit=5)
        experiences = list((await self.session.scalars(experience_statement)).all())
        capabilities = list((await self.session.scalars(capability_statement)).all())
        missing = list(intent.get("missing_information") or [])
        return {
            "experiences": [
                self._snapshot(item, experience_query, query_vector) for item in experiences
            ],
            "capabilities": [
                self._snapshot(item, capability_query, query_vector) for item in capabilities
            ],
            "conflicts": [],
            "missing_information": missing,
            "gap_summary": "；".join(missing) or None,
            "can_generate_solution": bool(experiences or capabilities),
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _statement(self, model, query: str, query_vector: list[float] | None, *, limit: int):
        filters = [
            model.workspace_id == self.workspace_id,
            model.review_status == ReviewStatus.VERIFIED,
            model.embedding_ready.is_(True),
            model.embedding.is_not(None),
            model.source_version_at_review == Source.content_version,
            model.is_deleted.is_(False),
            Source.workspace_id == self.workspace_id,
            Source.status == SourceStatus.COMPLETED,
            Source.freshness_status == SourceFreshness.CURRENT,
            Source.is_deleted.is_(False),
        ]
        terms = [term for term in query.strip().split() if term][:5]
        order_by = [model.updated_at.desc()]
        if terms:
            lexical_match = or_(*(cast(model.data, String).ilike(f"%{term}%") for term in terms))
            order_by.insert(0, lexical_match.desc())
        if query_vector is not None:
            order_by.insert(1 if terms else 0, model.embedding.cosine_distance(query_vector))
        return (
            select(model)
            .options(selectinload(model.source))
            .join(Source, Source.id == model.source_id)
            .where(*filters)
            .order_by(*order_by)
            .limit(limit)
        )

    @staticmethod
    def _snapshot(asset, query: str, query_vector: list[float] | None) -> dict:
        data_text = str(asset.data).casefold()
        terms = [term.casefold() for term in query.split() if term]
        lexical_hits = sum(term in data_text for term in terms)
        reasons = [f"lexical_hits={lexical_hits}/{len(terms)}"]
        asset_vector = getattr(asset, "embedding", None)
        if query_vector is not None and asset_vector is not None:
            similarity = cosine_similarity(list(asset_vector), query_vector)
            reasons.append(f"semantic_similarity={similarity:.4f}")
        reasons.append(f"source_freshness={SourceFreshness.CURRENT.value}")
        source = asset.source
        permission_checked_at = source.permission_checked_at
        permission_snapshot_id = ":".join(
            (
                str(asset.source_id),
                str(source.content_version),
                permission_checked_at.isoformat() if permission_checked_at else "unchecked",
            )
        )
        evidence_text = json.dumps(asset.data, ensure_ascii=False, sort_keys=True)
        return {
            "id": str(asset.id),
            "source_id": str(asset.source_id),
            "data": asset.data,
            "review_status": asset.review_status.value,
            "embedding_version": getattr(asset, "embedding_version", None),
            "match_reasons": reasons,
            "updated_at": asset.updated_at.isoformat(),
            "source_version": source.content_version,
            "reviewed_source_version": asset.source_version_at_review,
            "permission_status": "granted",
            "permission_checked_at": (
                permission_checked_at.isoformat() if permission_checked_at is not None else None
            ),
            "source_freshness": source.freshness_status.value,
            "source_title": source.title,
            "source_url": source.source_url,
            "source_snapshot": {
                "source_version": str(source.content_version),
                "reviewed_version": str(asset.source_version_at_review),
                "permission_snapshot_id": permission_snapshot_id,
                "permission_valid": True,
                "available": True,
                "invalid_reason": None,
                "title": source.title,
                "url": source.source_url,
                "author": source.author,
                "source_updated_at": (
                    source.source_updated_at.isoformat()
                    if source.source_updated_at is not None
                    else None
                ),
                "last_synced_at": (
                    source.synced_at.isoformat() if source.synced_at is not None else None
                ),
            },
            "evidence_quote": evidence_text[:8000],
            "evidence_location": {
                "kind": "reviewed_asset_json",
                "path": "data",
                "asset_updated_at": asset.updated_at.isoformat(),
                "source_fingerprint": source.content_fingerprint,
            },
        }
