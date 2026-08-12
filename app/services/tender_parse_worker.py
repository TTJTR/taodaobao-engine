import asyncio
import inspect
import multiprocessing
import queue
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models import TenderParseStatus, TenderRequirementStatus
from app.db.repositories import (
    RawArtifactRepository,
    TenderDocumentRepository,
    TenderParseVersionRepository,
    TenderRequirementRepository,
)
from app.integrations.docling_adapter import DoclingAdapter
from app.integrations.protocols import DocumentIR, DocumentParserAdapter

MAX_TENDER_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_PARSE_TIMEOUT_SECONDS = 300.0
SUPPORTED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class TenderParseWorker:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id,
        *,
        parser: DocumentParserAdapter | None = None,
        timeout_seconds: float = DEFAULT_PARSE_TIMEOUT_SECONDS,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.parser = parser or DoclingAdapter()
        self.timeout_seconds = timeout_seconds
        self.tenders = TenderDocumentRepository(session, workspace_id)
        self.artifacts = RawArtifactRepository(session, workspace_id)
        self.parse_versions = TenderParseVersionRepository(session, workspace_id)
        self.requirements = TenderRequirementRepository(session, workspace_id)

    async def parse(
        self,
        *,
        tender_id,
        raw_artifact_id,
        source: bytes | Path,
        mime_type: str,
        filename: str | None = None,
    ):
        tender = await self.tenders.get_for_update(tender_id)
        artifact = await self.artifacts.get(raw_artifact_id)
        if tender is None or artifact is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "招标文件或原始制品不存在", status_code=404)
        parse_version = await self.parse_versions.create(
            tender_id=tender.id,
            raw_artifact_id=artifact.id,
            version=await self.parse_versions.next_version(tender.id),
            parser_name=self.parser.parser_name,
            parser_version=self.parser.parser_version,
            document_schema_version="document-ir-v1",
            status=TenderParseStatus.PARSING,
            resource_usage={},
            started_at=datetime.now(UTC),
        )
        await self.session.commit()
        started = time.perf_counter()
        try:
            payload = _read_and_validate(source, mime_type, filename)
            document_ir = await asyncio.wait_for(
                self._invoke_parser(payload, mime_type, filename),
                timeout=self.timeout_seconds,
            )
            await self._complete(parse_version.id, tender.id, document_ir, len(payload), started)
        except TimeoutError as exc:
            await self._fail(parse_version.id, "TIMEOUT", "文档解析超过资源时限", started)
            raise AppError(
                ErrorCode.TENDER_PARSE_TIMEOUT,
                "文档解析超时",
                status_code=504,
                retryable=True,
            ) from exc
        except AppError as exc:
            await self._fail(parse_version.id, _failure_code(exc), exc.message, started)
            raise
        except Exception as exc:
            await self._fail(parse_version.id, "PARSER_FAILED", str(exc), started)
            raise AppError(
                ErrorCode.TENDER_PARSE_FAILED,
                "文档解析失败",
                status_code=422,
                retryable=False,
            ) from exc
        return await self.parse_versions.get(parse_version.id)

    async def _invoke_parser(
        self, payload: bytes, mime_type: str, filename: str | None
    ) -> DocumentIR:
        if isinstance(self.parser, DoclingAdapter) and self.parser._converter is None:
            return await _parse_docling_subprocess(payload, mime_type, filename)
        result = self.parser.parse(payload, mime_type, filename=filename)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _complete(
        self, parse_version_id, tender_id, document_ir: DocumentIR, byte_size: int, started: float
    ) -> None:
        parse_version = await self.parse_versions.get(parse_version_id)
        if parse_version is None:
            raise RuntimeError("parse version disappeared")
        await self.requirements.soft_delete_for_tender(tender_id)
        sequence = await self.requirements.next_sequence(tender_id)
        requirement_count = 0
        for node in document_ir.nodes:
            if node.node_type == "heading" or len(node.text.strip()) < 8:
                continue
            await self.requirements.create(
                tender_id=tender_id,
                parse_version_id=parse_version.id,
                sequence=sequence,
                version=1,
                requirement_text=node.text,
                category="general",
                mandatory=bool(re.search(r"必须|应当|不得|须|shall|must", node.text, re.I)),
                constraints={},
                ambiguities=[],
                source_location=node.location,
                status=TenderRequirementStatus.AI_DRAFT,
            )
            sequence += 1
            requirement_count += 1
        parse_version.status = TenderParseStatus.PARSED
        parse_version.document_ir = document_ir.to_dict()
        parse_version.resource_usage = {
            "byte_size": byte_size,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "node_count": len(document_ir.nodes),
            "requirement_count": requirement_count,
        }
        parse_version.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def _fail(
        self, parse_version_id, error_code: str, summary: str, started: float
    ) -> None:
        await self.session.rollback()
        parse_version = await self.parse_versions.get(parse_version_id)
        if parse_version is None:
            return
        parse_version.status = TenderParseStatus.FAILED
        parse_version.error_code = error_code
        parse_version.error_summary = summary[:1000]
        parse_version.resource_usage = {
            "duration_ms": round((time.perf_counter() - started) * 1000)
        }
        parse_version.completed_at = datetime.now(UTC)
        await self.session.commit()


def _read_and_validate(source: bytes | Path, mime_type: str, filename: str | None) -> bytes:
    if mime_type not in SUPPORTED_MIME:
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "不支持的招标文件格式", status_code=415)
    if isinstance(source, Path):
        size = source.stat().st_size
        if size > MAX_TENDER_FILE_BYTES:
            raise _too_large(size)
        payload = source.read_bytes()
    else:
        payload = source
        if len(payload) > MAX_TENDER_FILE_BYTES:
            raise _too_large(len(payload))
    if not payload:
        raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "招标文件为空", status_code=422)
    _validate_signature(payload, mime_type, filename)
    return payload


def _validate_signature(payload: bytes, mime_type: str, filename: str | None) -> None:
    suffix = Path(filename or "").suffix.lower()
    expected_suffixes = {
        "application/pdf": {"", ".pdf"},
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"", ".docx"},
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"", ".xlsx"},
    }[mime_type]
    if suffix not in expected_suffixes:
        raise AppError(
            ErrorCode.TENDER_FILE_UNSAFE,
            "文件扩展名与 MIME 类型不一致",
            status_code=422,
        )
    if mime_type == "application/pdf":
        if not payload.startswith(b"%PDF-"):
            raise AppError(ErrorCode.TENDER_FILE_UNSAFE, "文件头不是有效 PDF", status_code=422)
        return
    try:
        import io

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise AppError(
            ErrorCode.TENDER_FILE_UNSAFE,
            "Office 文件不是有效 ZIP",
            status_code=422,
        ) from exc
    required_prefix = "word/" if mime_type.endswith("wordprocessingml.document") else "xl/"
    has_required_part = any(name.startswith(required_prefix) for name in names)
    if "[Content_Types].xml" not in names or not has_required_part:
        raise AppError(
            ErrorCode.TENDER_FILE_UNSAFE,
            "Office 文件内容与 MIME 类型不一致",
            status_code=422,
        )


def _too_large(size: int) -> AppError:
    return AppError(
        ErrorCode.TENDER_FILE_TOO_LARGE,
        "招标文件超过 50 MiB",
        status_code=413,
        details={"byte_size": size, "max_byte_size": MAX_TENDER_FILE_BYTES},
    )


def _failure_code(error: AppError) -> str:
    if error.code == ErrorCode.TENDER_FILE_TOO_LARGE:
        return "FILE_TOO_LARGE"
    return error.code.value


def _docling_process(payload: bytes, mime_type: str, filename: str | None, output) -> None:
    try:
        output.put((True, DoclingAdapter().parse(payload, mime_type, filename=filename)))
    except BaseException as exc:
        output.put((False, f"{type(exc).__name__}: {exc}"))


async def _parse_docling_subprocess(
    payload: bytes, mime_type: str, filename: str | None
) -> DocumentIR:
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_docling_process,
        args=(payload, mime_type, filename, output),
        daemon=True,
    )
    process.start()
    try:
        while True:
            try:
                success, result = output.get_nowait()
                break
            except queue.Empty:
                if not process.is_alive():
                    raise RuntimeError(
                        f"Docling subprocess exited with code {process.exitcode}"
                    ) from None
                await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        raise
    finally:
        if not process.is_alive():
            process.join(timeout=1)
        output.close()
    if not success:
        raise RuntimeError(result)
    return result
