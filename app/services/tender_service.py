import base64
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime

from defusedxml import ElementTree
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    Capability,
    CustomerProfile,
    Experience,
    IntelligenceSnapshot,
    ResponseEvidenceStatus,
    ResponseMatrix,
    ResponseMatrixItem,
    ReviewStatus,
    Source,
    SourceFreshness,
    TenderDocument,
    TenderRequirement,
)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED = 30 * 1024 * 1024
SUPPORTED_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
}


def extract_tender_text(
    *, pasted_text: str | None, content_base64: str | None, mime_type: str | None
) -> str:
    if pasted_text:
        return pasted_text.strip()
    try:
        raw = base64.b64decode(content_base64 or "", validate=True)
    except ValueError as exc:
        raise AppError(
            ErrorCode.TENDER_FILE_UNSAFE, "文件不是有效的 Base64", status_code=422
        ) from exc
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "文件为空或超过 5 MiB", status_code=422)
    kind = SUPPORTED_MIME.get(mime_type or "")
    if kind == "txt":
        return raw.decode("utf-8", errors="strict").strip()
    if kind == "pdf":
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
            if len(reader.pages) > 300:
                raise ValueError("too many pages")
            return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception as exc:
            raise AppError(
                ErrorCode.TENDER_FILE_UNSAFE, "PDF 解析失败或文件不安全", status_code=422
            ) from exc
    if kind in {"docx", "xlsx", "pptx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 5000
                    or sum(item.file_size for item in entries) > MAX_ARCHIVE_UNCOMPRESSED
                ):
                    raise ValueError("archive expansion limit exceeded")
                prefixes = {
                    "docx": ("word/",),
                    "xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
                    "pptx": ("ppt/slides/",),
                }[kind]
                chunks: list[str] = []
                for entry in entries:
                    if not entry.filename.endswith(".xml") or not entry.filename.startswith(
                        prefixes
                    ):
                        continue
                    root = ElementTree.fromstring(archive.read(entry))
                    text = " ".join(
                        node.text.strip() for node in root.iter() if node.text and node.text.strip()
                    )
                    if text:
                        chunks.append(text)
                return "\n".join(chunks).strip()
        except (zipfile.BadZipFile, ValueError, ElementTree.ParseError) as exc:
            raise AppError(
                ErrorCode.TENDER_FILE_UNSAFE, "Office 文件解析失败或文件不安全", status_code=422
            ) from exc
    raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "不支持的招标文件类型", status_code=415)


def split_requirements(text: str) -> list[str]:
    candidates = re.split(r"(?:\r?\n)+|(?<=[。；;])", text)
    normalized = [" ".join(item.split()).strip(" -•\t") for item in candidates]
    requirements = [item for item in normalized if len(item) >= 8]
    return list(dict.fromkeys(requirements))[:300]


def _tokens(text: str) -> set[str]:
    latin = re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    bigrams = [token[index : index + 2] for token in chinese for index in range(len(token) - 1)]
    return set(latin + bigrams)


class TenderService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id

    def _filters(self, model) -> tuple:
        return model.workspace_id == self.workspace_id, model.is_deleted.is_(False)

    async def _get(self, model, entity_id: uuid.UUID):
        entity = await self.session.scalar(
            select(model).where(model.id == entity_id, *self._filters(model))
        )
        if entity is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        return entity

    async def create_tender(
        self,
        *,
        title: str,
        customer_profile_id: uuid.UUID | None,
        pasted_text: str | None,
        content_base64: str | None,
        filename: str | None,
        mime_type: str | None,
    ) -> TenderDocument:
        if customer_profile_id:
            await self._get(CustomerProfile, customer_profile_id)
        text = extract_tender_text(
            pasted_text=pasted_text, content_base64=content_base64, mime_type=mime_type
        )
        requirements = split_requirements(text)
        if not requirements:
            raise AppError(
                ErrorCode.TENDER_REQUIREMENT_EMPTY, "未提取到有效需求项", status_code=422
            )
        tender = TenderDocument(
            workspace_id=self.workspace_id,
            created_by_id=self.user_id,
            title=title.strip(),
            customer_profile_id=customer_profile_id,
            source_filename=filename,
            source_mime_type=mime_type or "text/plain",
            source_fingerprint=hashlib.sha256(text.encode()).hexdigest(),
            content_text=text,
            status="ready",
        )
        self.session.add(tender)
        await self.session.flush()
        for sequence, requirement in enumerate(requirements, 1):
            self.session.add(
                TenderRequirement(
                    workspace_id=self.workspace_id,
                    tender_id=tender.id,
                    sequence=sequence,
                    requirement_text=requirement,
                    category="general",
                    mandatory=bool(re.search(r"必须|应当|不得|须|shall|must", requirement, re.I)),
                    source_location={"sequence": sequence},
                )
            )
        await self.session.commit()
        await self.session.refresh(tender)
        return tender

    async def list_tenders(self, page: int, page_size: int) -> tuple[list[TenderDocument], int]:
        filters = self._filters(TenderDocument)
        rows = await self.session.scalars(
            select(TenderDocument)
            .where(*filters)
            .order_by(TenderDocument.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(TenderDocument).where(*filters)
        )
        return list(rows), int(total or 0)

    async def get_tender(self, tender_id: uuid.UUID) -> TenderDocument:
        return await self._get(TenderDocument, tender_id)

    async def list_requirements(self, tender_id: uuid.UUID) -> list[TenderRequirement]:
        await self.get_tender(tender_id)
        return list(
            await self.session.scalars(
                select(TenderRequirement)
                .where(TenderRequirement.tender_id == tender_id, *self._filters(TenderRequirement))
                .order_by(TenderRequirement.sequence)
            )
        )

    async def create_matrix(
        self,
        tender_id: uuid.UUID,
        experience_ids: list[uuid.UUID],
        capability_ids: list[uuid.UUID],
        intelligence_snapshot_id: uuid.UUID | None,
    ) -> ResponseMatrix:
        await self.get_tender(tender_id)
        requirements = await self.list_requirements(tender_id)
        evidence = await self._load_internal_evidence(experience_ids, capability_ids)
        intelligence = None
        if intelligence_snapshot_id:
            intelligence = await self._get(IntelligenceSnapshot, intelligence_snapshot_id)
        snapshot = {
            "captured_at": datetime.now(UTC).isoformat(),
            "internal_evidence": evidence,
            "external_intelligence_snapshot_id": str(intelligence.id) if intelligence else None,
            "boundary": (
                "external intelligence is context only and cannot prove enterprise capability"
            ),
        }
        matrix = ResponseMatrix(
            workspace_id=self.workspace_id,
            tender_id=tender_id,
            created_by_id=self.user_id,
            version=1,
            status="draft",
            evidence_snapshot=snapshot,
        )
        self.session.add(matrix)
        await self.session.flush()
        for requirement in requirements:
            matched = self._match_evidence(requirement.requirement_text, evidence)
            if matched:
                status = ResponseEvidenceStatus.SUPPORTED
                response = (
                    "基于已校验企业资料，可以响应本项要求；正式承诺范围以所附证据和人工审核为准。"
                )
                risks: list[str] = []
            else:
                status = ResponseEvidenceStatus.MISSING_EVIDENCE
                response = "当前未找到可用的企业经验或原子能力依据，暂不作能力承诺。"
                risks = ["缺少企业内部依据，需要补充材料或人工确认"]
            self.session.add(
                ResponseMatrixItem(
                    workspace_id=self.workspace_id,
                    matrix_id=matrix.id,
                    requirement_id=requirement.id,
                    response_text=response,
                    evidence_status=status,
                    evidence_refs=matched,
                    risks=risks,
                    review_status="pending",
                )
            )
        await self.session.commit()
        await self.session.refresh(matrix)
        return matrix

    async def get_matrix(
        self, matrix_id: uuid.UUID
    ) -> tuple[ResponseMatrix, list[ResponseMatrixItem]]:
        matrix = await self._get(ResponseMatrix, matrix_id)
        items = list(
            await self.session.scalars(
                select(ResponseMatrixItem)
                .where(
                    ResponseMatrixItem.matrix_id == matrix_id, *self._filters(ResponseMatrixItem)
                )
                .order_by(ResponseMatrixItem.created_at)
            )
        )
        return matrix, items

    async def update_item(
        self,
        matrix_id: uuid.UUID,
        item_id: uuid.UUID,
        response_text: str | None,
        risks: list[str] | None,
    ) -> ResponseMatrixItem:
        await self._get(ResponseMatrix, matrix_id)
        item = await self._get(ResponseMatrixItem, item_id)
        if item.matrix_id != matrix_id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "响应项不属于该矩阵", status_code=409)
        if response_text is not None:
            item.response_text = response_text
        if risks is not None:
            item.risks = risks
        item.review_status = "pending"
        item.reviewer_id = None
        item.review_note = None
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def review_item(
        self, matrix_id: uuid.UUID, item_id: uuid.UUID, action: str, note: str | None
    ) -> ResponseMatrixItem:
        item = await self.update_item(matrix_id, item_id, None, None)
        if action == "accept" and item.evidence_status == ResponseEvidenceStatus.MISSING_EVIDENCE:
            raise AppError(
                ErrorCode.RESPONSE_EVIDENCE_INVALID,
                "无企业依据的响应项不能审核为可承诺",
                status_code=409,
            )
        item.review_status = {"accept": "accepted", "reject": "rejected"}.get(
            action, "needs_revision"
        )
        item.reviewer_id = self.user_id
        item.review_note = note
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def _load_internal_evidence(
        self, experience_ids: list[uuid.UUID], capability_ids: list[uuid.UUID]
    ) -> list[dict]:
        evidence: list[dict] = []
        pairs = (
            (Experience, "experience", experience_ids),
            (Capability, "capability", capability_ids),
        )
        for model, kind, ids in pairs:
            if not ids:
                continue
            rows = list(
                await self.session.scalars(
                    select(model)
                    .join(Source, model.source_id == Source.id)
                    .where(
                        model.workspace_id == self.workspace_id,
                        model.id.in_(list(dict.fromkeys(ids))),
                        model.is_deleted.is_(False),
                        model.review_status == ReviewStatus.VERIFIED,
                        model.embedding_ready.is_(True),
                        Source.workspace_id == self.workspace_id,
                        Source.is_deleted.is_(False),
                        Source.freshness_status == SourceFreshness.CURRENT,
                        model.source_version_at_review == Source.content_version,
                    )
                )
            )
            if len(rows) != len(set(ids)):
                raise AppError(
                    ErrorCode.RESPONSE_EVIDENCE_INVALID,
                    f"{kind} 包含未校验、已失效或跨工作区资产",
                    status_code=409,
                )
            for row in rows:
                source = await self.session.scalar(select(Source).where(Source.id == row.source_id))
                evidence.append(
                    {
                        "type": kind,
                        "id": str(row.id),
                        "data": row.data,
                        "source_snapshot": {
                            "source_id": str(source.id),
                            "title": source.title,
                            "version": source.content_version,
                            "content_version": source.content_version,
                            "reviewed_version": row.source_version_at_review,
                            "review_status": row.review_status.value,
                            "freshness": source.freshness_status.value,
                            "permission_status": "allowed",
                            "available": True,
                            "permission_checked_at": source.permission_checked_at.isoformat()
                            if source.permission_checked_at
                            else None,
                            "source_updated_at": source.source_updated_at.isoformat()
                            if source.source_updated_at
                            else None,
                            "captured_at": datetime.now(UTC).isoformat(),
                        },
                    }
                )
        return evidence

    @staticmethod
    def _match_evidence(requirement: str, evidence: list[dict]) -> list[dict]:
        requirement_tokens = _tokens(requirement)
        scored = []
        for row in evidence:
            overlap = requirement_tokens & _tokens(json.dumps(row["data"], ensure_ascii=False))
            if overlap:
                scored.append((len(overlap), row))
        return [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[:5]]
