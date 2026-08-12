import base64
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

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
    RawArtifact,
    ResponseEvidenceStatus,
    ResponseMatrix,
    ResponseMatrixItem,
    ReviewStatus,
    Source,
    SourceFreshness,
    TenderDocument,
    TenderParseStatus,
    TenderParseVersion,
    TenderRequirement,
    TenderRequirementStatus,
    TenderRequirementVersion,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.services.retrieval_service import RetrievalService

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


def _validate_document_location(location) -> None:
    if not isinstance(location, dict):
        raise AppError(ErrorCode.EVIDENCE_LOCATION_INVALID, "要求缺少原文位置", status_code=422)
    if location.get("schema_version") != "document-location-v1":
        raise AppError(ErrorCode.EVIDENCE_LOCATION_INVALID, "原文位置版本无效", status_code=422)
    if not re.fullmatch(r"[a-f0-9]{64}", str(location.get("quote_hash", ""))):
        raise AppError(ErrorCode.EVIDENCE_LOCATION_INVALID, "原文引文指纹无效", status_code=422)
    required = {
        "pdf_page": ("page",),
        "docx_paragraph": ("paragraph_index",),
        "xlsx_cell": ("sheet", "cell_range"),
        "pptx_shape": ("slide", "shape_id"),
        "plain_text": ("start_offset", "end_offset"),
    }
    kind = location.get("kind")
    if kind not in required or any(location.get(field) is None for field in required[kind]):
        raise AppError(ErrorCode.EVIDENCE_LOCATION_INVALID, "原文位置字段不完整", status_code=422)


def _evidence_link(row: dict) -> dict:
    return {
        "asset_id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "source_snapshot": row.get("source_snapshot", {}),
        "match_reasons": row.get("match_reasons", []),
    }


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

    async def queue_parse(self, tender_id: uuid.UUID, raw_artifact_id: uuid.UUID) -> WorkflowTask:
        await self.get_tender(tender_id)
        artifact = await self._get(RawArtifact, raw_artifact_id)
        if not artifact.storage_uri:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "原始制品尚未持久化，不能排队解析",
                status_code=409,
            )
        task = await self.session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "tender_parse",
                WorkflowTask.target_id == tender_id,
                *self._filters(WorkflowTask),
            )
        )
        if task is None:
            task = WorkflowTask(
                workspace_id=self.workspace_id,
                kind="tender_parse",
                target_id=tender_id,
                status=WorkflowTaskStatus.QUEUED,
                stage="queued",
                trace_id=str(uuid.uuid4()),
                payload={"raw_artifact_id": str(artifact.id)},
                attempt_count=0,
                max_attempts=3,
                available_at=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(hours=1),
            )
            self.session.add(task)
        elif task.payload.get("raw_artifact_id") != str(artifact.id):
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "该招标文件已有其他制品的解析任务",
                status_code=409,
            )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def list_requirements(self, tender_id: uuid.UUID) -> list[TenderRequirement]:
        await self.get_tender(tender_id)
        return list(
            await self.session.scalars(
                select(TenderRequirement)
                .where(TenderRequirement.tender_id == tender_id, *self._filters(TenderRequirement))
                .order_by(TenderRequirement.sequence)
            )
        )

    async def update_requirement(self, tender_id, requirement_id, expected_version, **changes):
        row = await self._locked_requirement(tender_id, requirement_id)
        self._check_requirement_version(row, expected_version)
        await self._ensure_requirement_snapshot(row)
        for field, value in changes.items():
            if field in {
                "requirement_text",
                "category",
                "mandatory",
                "constraints",
                "ambiguities",
            } and value is None:
                raise AppError(ErrorCode.VALIDATION_FAILED, "要求必填字段不能为空", status_code=422)
            setattr(row, field, value)
        row.status = TenderRequirementStatus.EDITED
        row.version += 1
        await self._snapshot_requirement(row, "edited")
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def confirm_requirement(self, tender_id, requirement_id, expected_version):
        row = await self._locked_requirement(tender_id, requirement_id)
        self._check_requirement_version(row, expected_version)
        await self._ensure_requirement_snapshot(row)
        row.status = TenderRequirementStatus.CONFIRMED
        row.confirmed_by_id = self.user_id
        row.confirmed_at = datetime.now(UTC)
        row.version += 1
        await self._snapshot_requirement(row, "confirmed")
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete_requirement(self, tender_id, requirement_id, expected_version):
        row = await self._locked_requirement(tender_id, requirement_id)
        self._check_requirement_version(row, expected_version)
        await self._ensure_requirement_unused([row.id])
        await self._ensure_requirement_snapshot(row)
        row.is_deleted = True
        row.version += 1
        await self._snapshot_requirement(row, "deleted")
        await self.session.commit()

    async def merge_requirements(
        self, tender_id, requirement_ids, expected_versions, requirement_text
    ):
        ids = list(dict.fromkeys(requirement_ids))
        if len(ids) < 2:
            raise AppError(ErrorCode.VALIDATION_FAILED, "至少选择两条不同要求", status_code=422)
        rows = list(await self.session.scalars(
            select(TenderRequirement).where(
                TenderRequirement.id.in_(ids), TenderRequirement.tender_id == tender_id,
                *self._filters(TenderRequirement),
            ).order_by(TenderRequirement.sequence).with_for_update()
        ))
        if len(rows) != len(ids):
            raise AppError(ErrorCode.VALIDATION_FAILED, "要求不存在或跨工作区", status_code=404)
        await self._ensure_requirement_unused(ids)
        for row in rows:
            self._check_requirement_version(row, expected_versions.get(str(row.id), 0))
            await self._ensure_requirement_snapshot(row)
        primary = rows[0]
        primary.requirement_text = requirement_text
        primary.constraints = {
            **primary.constraints,
            "merged_requirement_ids": [str(item.id) for item in rows],
            "merged_source_locations": [item.source_location for item in rows[1:]],
        }
        primary.status = TenderRequirementStatus.EDITED
        primary.version += 1
        await self._snapshot_requirement(primary, "merged")
        for row in rows[1:]:
            row.status = TenderRequirementStatus.SUPERSEDED
            row.is_deleted = True
            row.version += 1
            await self._snapshot_requirement(row, "superseded")
        await self.session.commit()
        await self.session.refresh(primary)
        return primary

    async def split_requirement(self, tender_id, requirement_id, expected_version, items):
        parent = await self._locked_requirement(tender_id, requirement_id)
        self._check_requirement_version(parent, expected_version)
        await self._ensure_requirement_unused([parent.id])
        await self._ensure_requirement_snapshot(parent)
        sequence = int(await self.session.scalar(select(func.max(TenderRequirement.sequence)).where(
            TenderRequirement.tender_id == tender_id,
            TenderRequirement.workspace_id == self.workspace_id,
        )) or 0) + 1
        created = []
        for item in items:
            row = TenderRequirement(
                workspace_id=self.workspace_id, tender_id=tender_id,
                parse_version_id=parent.parse_version_id, sequence=sequence, version=1,
                requirement_text=item.requirement_text,
                category=item.category or parent.category,
                mandatory=parent.mandatory if item.mandatory is None else item.mandatory,
                acceptance_condition=parent.acceptance_condition,
                constraints={**parent.constraints, "split_from_requirement_id": str(parent.id)},
                ambiguities=list(parent.ambiguities), source_location=dict(parent.source_location),
                status=TenderRequirementStatus.EDITED,
            )
            self.session.add(row)
            await self.session.flush()
            await self._snapshot_requirement(row, "created")
            created.append(row)
            sequence += 1
        parent.status = TenderRequirementStatus.SUPERSEDED
        parent.is_deleted = True
        parent.version += 1
        await self._snapshot_requirement(parent, "split")
        await self.session.commit()
        return created

    async def _locked_requirement(self, tender_id, requirement_id):
        await self.get_tender(tender_id)
        row = await self.session.scalar(select(TenderRequirement).where(
            TenderRequirement.id == requirement_id, TenderRequirement.tender_id == tender_id,
            *self._filters(TenderRequirement),
        ).with_for_update())
        if row is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "招标要求不存在", status_code=404)
        return row

    @staticmethod
    def _check_requirement_version(row, expected):
        if row.version != expected:
            raise AppError(
                ErrorCode.RESPONSE_VERSION_CONFLICT,
                "招标要求版本已更新",
                status_code=409,
            )

    async def _ensure_requirement_snapshot(self, row):
        existing = await self.session.scalar(
            select(TenderRequirementVersion.id).where(
                TenderRequirementVersion.requirement_id == row.id,
                TenderRequirementVersion.version == row.version,
                *self._filters(TenderRequirementVersion),
            )
        )
        if existing is None:
            await self._snapshot_requirement(row, "created")

    async def _snapshot_requirement(self, row, change_type):
        self.session.add(TenderRequirementVersion(
            workspace_id=self.workspace_id, requirement_id=row.id, version=row.version,
            changed_by_id=self.user_id, change_type=change_type,
            requirement_snapshot={
                "requirement_text": row.requirement_text, "category": row.category,
                "mandatory": row.mandatory, "acceptance_condition": row.acceptance_condition,
                "constraints": row.constraints, "ambiguities": row.ambiguities,
                "status": row.status.value,
            }, source_location=dict(row.source_location),
        ))

    async def _ensure_requirement_unused(self, ids):
        used = await self.session.scalar(select(ResponseMatrixItem.id).where(
            ResponseMatrixItem.requirement_id.in_(ids), *self._filters(ResponseMatrixItem)
        ).limit(1))
        if used:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "要求已被响应矩阵引用，不能重组或删除",
                status_code=409,
            )

    async def breakdown_requirements(self, tender_id: uuid.UUID) -> list[TenderRequirement]:
        await self.get_tender(tender_id)
        parsed = await self.session.scalar(
            select(TenderParseVersion)
            .where(
                TenderParseVersion.tender_id == tender_id,
                TenderParseVersion.workspace_id == self.workspace_id,
                TenderParseVersion.is_deleted.is_(False),
                TenderParseVersion.status == TenderParseStatus.PARSED,
            )
            .order_by(TenderParseVersion.version.desc())
            .limit(1)
        )
        if parsed is None or not parsed.document_ir:
            raise AppError(
                ErrorCode.TENDER_REQUIREMENT_EMPTY,
                "没有成功的文档解析版本",
                status_code=409,
            )
        existing = await self.list_requirements(tender_id)
        for row in existing:
            row.is_deleted = True
        next_sequence = int(
            await self.session.scalar(
                select(func.max(TenderRequirement.sequence)).where(
                    TenderRequirement.tender_id == tender_id,
                    TenderRequirement.workspace_id == self.workspace_id,
                )
            )
            or 0
        ) + 1
        created = []
        for node in parsed.document_ir.get("nodes", []):
            text_value = str(node.get("text", "")).strip()
            location = node.get("location")
            if node.get("node_type") == "heading" or len(text_value) < 8:
                continue
            _validate_document_location(location)
            row = TenderRequirement(
                workspace_id=self.workspace_id,
                tender_id=tender_id,
                parse_version_id=parsed.id,
                sequence=next_sequence,
                version=1,
                requirement_text=text_value,
                category="general",
                mandatory=bool(re.search(r"必须|应当|不得|须|shall|must", text_value, re.I)),
                constraints={},
                ambiguities=[],
                source_location=location,
                status="ai_draft",
            )
            self.session.add(row)
            created.append(row)
            next_sequence += 1
        if not created:
            raise AppError(ErrorCode.TENDER_REQUIREMENT_EMPTY, "未拆解出有效要求", status_code=422)
        await self.session.commit()
        return created

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
            retrieved = await RetrievalService(self.session, self.workspace_id).retrieve(
                requirement.requirement_text
            )
            automatic = [
                {"type": "experience", **item} for item in retrieved["experiences"]
            ] + [{"type": "capability", **item} for item in retrieved["capabilities"]]
            matched = self._match_evidence(requirement.requirement_text, [*evidence, *automatic])
            exp_links = [_evidence_link(row) for row in matched if row["type"] == "experience"]
            cap_links = [_evidence_link(row) for row in matched if row["type"] == "capability"]
            external_links = (
                [{"snapshot_id": str(intelligence.id), "boundary": "external_context_only"}]
                if intelligence
                else []
            )
            if matched:
                status = ResponseEvidenceStatus.SUPPORTED
                response = (
                    "基于已校验企业资料，可以响应本项要求；正式承诺范围以所附证据和人工审核为准。"
                )
                risks: list[str] = []
                risk_flags: list[str] = []
            else:
                status = ResponseEvidenceStatus.MISSING_EVIDENCE
                response = "当前未找到可用的企业经验或原子能力依据，暂不作能力承诺。"
                risks = ["缺少企业内部依据，需要补充材料或人工确认"]
                risk_flags = ["missing_evidence"]
            self.session.add(
                ResponseMatrixItem(
                    workspace_id=self.workspace_id,
                    matrix_id=matrix.id,
                    requirement_id=requirement.id,
                    response_text=response,
                    ai_draft=response,
                    current_answer=response,
                    evidence_status=status,
                    evidence_refs=matched,
                    risks=risks,
                    internal_exp_links=exp_links,
                    internal_cap_links=cap_links,
                    external_ctx_links=external_links,
                    risk_flags=risk_flags,
                    review_status="pending",
                    version=1,
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
        expected_version: int,
    ) -> ResponseMatrixItem:
        await self._get(ResponseMatrix, matrix_id)
        item = await self._locked_item(item_id)
        if item.matrix_id != matrix_id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "响应项不属于该矩阵", status_code=409)
        self._check_version(item, expected_version)
        if response_text is not None:
            item.response_text = response_text
            item.current_answer = response_text
        if risks is not None:
            item.risks = risks
        item.review_status = "pending"
        item.reviewer_id = None
        item.review_note = None
        item.approved_at = None
        item.version += 1
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def review_item(
        self, matrix_id: uuid.UUID, item_id: uuid.UUID, action: str, note: str | None,
        expected_version: int, current_answer: str | None,
    ) -> ResponseMatrixItem:
        await self._get(ResponseMatrix, matrix_id)
        item = await self._locked_item(item_id)
        if item.matrix_id != matrix_id:
            raise AppError(ErrorCode.VALIDATION_FAILED, "响应项不属于该矩阵", status_code=409)
        self._check_version(item, expected_version)
        self._validate_review_action(item, action, current_answer)
        if current_answer is not None:
            item.current_answer = current_answer
            item.response_text = current_answer
        item.review_status = {
            "approve": "approved", "edit_and_approve": "approved",
            "reject": "rejected", "needs_evidence": "needs_evidence",
        }[action]
        item.reviewer_id = self.user_id
        item.review_note = note
        item.approved_at = datetime.now(UTC) if item.review_status == "approved" else None
        item.version += 1
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def _locked_item(self, item_id: uuid.UUID) -> ResponseMatrixItem:
        item = await self.session.scalar(
            select(ResponseMatrixItem).where(
                ResponseMatrixItem.id == item_id, *self._filters(ResponseMatrixItem)
            ).with_for_update()
        )
        if item is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "资源不存在", status_code=404)
        return item

    @staticmethod
    def _check_version(item: ResponseMatrixItem, expected: int) -> None:
        if item.version != expected:
            raise AppError(ErrorCode.RESPONSE_VERSION_CONFLICT, "响应项版本已更新", status_code=409)

    @staticmethod
    def _validate_review_action(
        item: ResponseMatrixItem, action: str, current_answer: str | None
    ) -> None:
        if action == "edit_and_approve" and not current_answer:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "编辑后接受必须提供响应正文",
                status_code=422,
            )
        if action in {"approve", "edit_and_approve"} and "missing_evidence" in item.risk_flags:
            raise AppError(
                ErrorCode.RESPONSE_EVIDENCE_INVALID,
                "无企业依据的响应项不能审核为可承诺",
                status_code=409,
            )

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
