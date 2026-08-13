import hashlib
import importlib.metadata
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.integrations.protocols import DocumentIR, DocumentLocation, DocumentNode

MIME_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


class DoclingAdapter:
    parser_name = "docling"

    def __init__(self, converter: Any | None = None) -> None:
        self._converter = converter
        try:
            self.parser_version = importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            self.parser_version = "not-installed"

    def parse(
        self,
        source: bytes | Path,
        mime_type: str,
        *,
        filename: str | None = None,
    ) -> DocumentIR:
        if mime_type not in MIME_SUFFIXES:
            raise ValueError(f"unsupported parser MIME type: {mime_type}")
        path, temporary = self._materialize(source, mime_type, filename)
        try:
            converter = self._converter or self._load_converter()
            result = converter.convert(path)
            document = _value(result, "document", result)
            return self.map_document(document, mime_type)
        finally:
            if temporary:
                path.unlink(missing_ok=True)

    def map_document(self, document: Any, mime_type: str) -> DocumentIR:
        nodes: list[DocumentNode] = []
        warnings: list[str] = []
        headings: list[str] = []
        text_offset = 0
        for index, item in enumerate(_items(document)):
            node_type = _node_type(item)
            text = _item_text(item, node_type, document)
            if not text:
                continue
            if node_type == "heading":
                level = max(1, int(_value(item, "level", 1) or 1))
                headings[:] = headings[: level - 1]
                headings.append(text)
            location = _map_location(
                item,
                mime_type,
                index=index,
                text=text,
                section_path=headings,
                text_offset=text_offset,
            )
            if node_type == "table" and location["kind"] == "plain_text":
                warnings.append(f"table {index} has no native location; used plain_text fallback")
            nodes.append(DocumentNode(text=text, node_type=node_type, location=location))
            text_offset += len(text) + 1
        return DocumentIR(
            nodes=tuple(nodes),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _load_converter() -> Any:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise RuntimeError(
                "Docling is not installed; install the 'v2-parser' optional dependency"
            ) from exc
        return DocumentConverter()

    @staticmethod
    def _materialize(
        source: bytes | Path, mime_type: str, filename: str | None
    ) -> tuple[Path, bool]:
        if isinstance(source, Path):
            return source, False
        suffix = Path(filename or "").suffix.lower() or MIME_SUFFIXES[mime_type]
        handle = tempfile.NamedTemporaryFile(prefix="tender-", suffix=suffix, delete=False)
        try:
            handle.write(source)
        finally:
            handle.close()
        return Path(handle.name), True


def _items(document: Any) -> Iterable[Any]:
    iterator = _value(document, "iterate_items")
    if callable(iterator):
        for entry in iterator():
            yield entry[0] if isinstance(entry, tuple) else entry
        return
    for name in ("body", "texts", "tables", "items"):
        value = _value(document, name)
        if isinstance(value, Iterable) and not isinstance(value, str | bytes | dict):
            yield from value


def _node_type(item: Any) -> str:
    label = str(_value(item, "label", _value(item, "type", "paragraph"))).lower()
    if "table" in label:
        return "table"
    if any(token in label for token in ("title", "heading", "section_header")):
        return "heading"
    return "paragraph"


def _item_text(item: Any, node_type: str, document: Any) -> str:
    if node_type == "table":
        for name in ("export_to_markdown", "export_to_html"):
            exporter = _value(item, name)
            if callable(exporter):
                try:
                    value = exporter(document)
                except TypeError:
                    value = exporter()
                if value:
                    return str(value).strip()
    return str(_value(item, "text", "") or "").strip()


def _map_location(
    item: Any,
    mime_type: str,
    *,
    index: int,
    text: str,
    section_path: list[str],
    text_offset: int,
) -> DocumentLocation:
    quote_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    common: DocumentLocation = {
        "schema_version": "document-location-v1",
        "quote_hash": quote_hash,
    }
    if section_path:
        common["section_path"] = list(section_path)
    provenance = _first(_value(item, "prov", ()))
    if mime_type == "application/pdf":
        page = _value(provenance, "page_no", _value(item, "page_no"))
        bbox = _bounding_box(_value(provenance, "bbox", _value(item, "bbox")))
        if page is not None:
            return {**common, "kind": "pdf_page", "page": int(page), "bounding_box": bbox}
    elif mime_type.endswith("wordprocessingml.document"):
        return {
            **common,
            "kind": "docx_paragraph",
            "paragraph_index": int(_value(item, "paragraph_index", index)),
            "table_index": _optional_int(_value(item, "table_index")),
            "row_index": _optional_int(_value(item, "row_index")),
            "column_index": _optional_int(_value(item, "column_index")),
        }
    elif mime_type.endswith("spreadsheetml.sheet"):
        sheet = _value(item, "sheet_name", _value(item, "sheet"))
        cell_range = _value(item, "cell_range")
        if sheet and cell_range:
            return {
                **common,
                "kind": "xlsx_cell",
                "sheet": str(sheet),
                "cell_range": str(cell_range),
            }
    return {
        **common,
        "kind": "plain_text",
        "start_offset": text_offset,
        "end_offset": text_offset + len(text),
    }


def _bounding_box(value: Any) -> list[float] | None:
    if value is None:
        return None
    coordinates = [
        _value(value, "l", _value(value, "x0")),
        _value(value, "t", _value(value, "y0")),
        _value(value, "r", _value(value, "x1")),
        _value(value, "b", _value(value, "y1")),
    ]
    if any(coordinate is None for coordinate in coordinates):
        if isinstance(value, list | tuple) and len(value) == 4:
            coordinates = list(value)
        else:
            return None
    result = [float(coordinate) for coordinate in coordinates]
    return result if all(coordinate >= 0 for coordinate in result) else None


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _first(value: Any) -> Any:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return value


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
