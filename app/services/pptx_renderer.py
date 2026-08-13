import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.schemas.presentation import PositionedPresentationSpec, VisualStyleProfileData


class PPTXRenderError(RuntimeError):
    """A deterministic PPTX render failed closed."""


@dataclass(frozen=True)
class PPTXRenderResult:
    path: Path
    slide_count: int
    size_bytes: int


class PPTXRenderer:
    """Invoke the fixed PptxGenJS renderer without shell, network, database, or AI access."""

    def __init__(
        self,
        *,
        node_binary: str = "node",
        script_path: Path | None = None,
        output_directory: Path | None = None,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        self.node_binary = node_binary
        self.script_path = script_path or (
            Path(__file__).parents[2] / "sidecar" / "pptx-renderer" / "render.mjs"
        )
        self.output_directory = output_directory or settings.presentation_export_dir
        self.timeout_seconds = timeout_seconds or settings.pptx_renderer_timeout_seconds
        self.max_output_bytes = max_output_bytes or settings.pptx_renderer_max_output_bytes

    async def render(
        self,
        spec: PositionedPresentationSpec,
        style: VisualStyleProfileData,
        *,
        artifact_id: str,
    ) -> PPTXRenderResult:
        if not self.script_path.is_file():
            raise PPTXRenderError("PPTX renderer script is unavailable")
        self.output_directory.mkdir(parents=True, exist_ok=True)
        final_path = (self.output_directory / f"{artifact_id}.pptx").resolve()
        output_root = self.output_directory.resolve()
        if final_path.parent != output_root:
            raise PPTXRenderError("PPTX output escaped the configured directory")

        payload = json.dumps(
            {"spec": spec.model_dump(mode="json"), "style": style.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"{artifact_id}-",
                suffix=".pptx",
                dir=output_root,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            process = await asyncio.create_subprocess_exec(
                self.node_binary,
                str(self.script_path.resolve()),
                str(temporary_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(payload), timeout=self.timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise PPTXRenderError("PPTX renderer timed out") from exc
            if process.returncode != 0:
                summary = stderr.decode("utf-8", errors="replace")[:500].strip()
                raise PPTXRenderError(f"PPTX renderer rejected the spec: {summary}")
            try:
                report = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise PPTXRenderError("PPTX renderer returned an invalid report") from exc
            size_bytes = temporary_path.stat().st_size
            if size_bytes <= 0 or size_bytes > self.max_output_bytes:
                raise PPTXRenderError("PPTX output size is outside the allowed range")
            if report.get("slides") != len(spec.slides) or report.get("bytes") != size_bytes:
                raise PPTXRenderError("PPTX renderer report does not match its output")
            os.replace(temporary_path, final_path)
            temporary_path = None
            return PPTXRenderResult(
                path=final_path,
                slide_count=len(spec.slides),
                size_bytes=size_bytes,
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
