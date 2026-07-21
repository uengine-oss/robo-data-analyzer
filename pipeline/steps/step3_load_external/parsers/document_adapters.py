"""Bounded optional document adapters with a killable subprocess boundary.

Heavy parsers are never imported when Analyzer starts.  The parent process
checks file size and dependency availability, then runs the parser in a child
process that is killed on timeout.  Every successful adapter emits Markdown so
the existing document hierarchy parser remains the single graph-model owner.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Literal


_PDF_SUFFIXES = frozenset({".pdf"})
_XLSX_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xltx", ".xltm"})
_DOCX_SUFFIXES = frozenset({".docx"})
_HWP_SUFFIXES = frozenset({".hwp", ".hwpx"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
DOCUMENT_ADAPTER_SUFFIXES = frozenset().union(
    _PDF_SUFFIXES, _XLSX_SUFFIXES, _DOCX_SUFFIXES, _HWP_SUFFIXES, _IMAGE_SUFFIXES,
)


@dataclass(frozen=True)
class DocumentLimits:
    max_file_bytes: int = 64 * 1024 * 1024
    max_output_chars: int = 2_000_000
    max_pages: int = 500
    max_sheets: int = 100
    max_rows_per_sheet: int = 10_000
    max_total_cells: int = 250_000
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class AdapterStatus:
    name: str
    dependency: str
    license: str
    state: Literal["available", "missing", "unsupported"]
    reason: str = ""


@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str
    adapter: str
    page_count: int = 0
    requires_ocr: bool = False
    used_ocr: bool = False


class DocumentAdapterError(RuntimeError):
    pass


class OptionalDependencyMissing(DocumentAdapterError):
    pass


class DocumentLimitExceeded(DocumentAdapterError):
    pass


class DocumentEncrypted(DocumentAdapterError):
    pass


class DocumentTimeout(DocumentAdapterError):
    pass


class DocumentMalformed(DocumentAdapterError):
    pass


_ADAPTERS = (
    (_PDF_SUFFIXES, "pymupdf", "pymupdf", "AGPL-3.0-or-commercial"),
    (_XLSX_SUFFIXES, "openpyxl", "openpyxl", "MIT"),
    (_DOCX_SUFFIXES, "python-docx", "docx", "MIT"),
    (_HWP_SUFFIXES, "hwpxkit", "hwpxkit", "MIT"),
    (_IMAGE_SUFFIXES, "PaddleOCR-VL-1.5", "paddleocr", "Apache-2.0"),
)


def _adapter_spec(extension: str) -> tuple[str, str, str] | None:
    suffix = extension.lower()
    for suffixes, name, module, license_name in _ADAPTERS:
        if suffix in suffixes:
            return name, module, license_name
    return None


def adapter_status(extension: str) -> AdapterStatus:
    spec = _adapter_spec(extension)
    if spec is None:
        return AdapterStatus("", "", "", "unsupported", "no document adapter")
    name, module, license_name = spec
    required_modules = {
        "openpyxl": ("openpyxl", "defusedxml"),
        "paddleocr": ("paddle", "paddleocr"),
    }.get(module, (module,))
    available = all(importlib.util.find_spec(name) is not None for name in required_modules)
    dependency = {
        "openpyxl": "openpyxl+defusedxml",
        "paddleocr": "paddlepaddle+paddleocr[doc-parser]",
    }.get(module, module)
    return AdapterStatus(
        name,
        dependency,
        license_name,
        "available" if available else "missing",
        "" if available else f"optional dependency not installed: {dependency}",
    )


def _bounded(markdown: str, limits: DocumentLimits) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > limits.max_output_chars:
        raise DocumentLimitExceeded(
            f"document output exceeds {limits.max_output_chars} characters"
        )
    return normalized


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def _markdown_table(rows: list[list[str]]) -> str:
    width = max((len(row) for row in rows), default=0)
    if width == 0:
        return ""
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    body = padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _extract_xlsx(path: Path, openpyxl_module, limits: DocumentLimits) -> ExtractedDocument:
    workbook = openpyxl_module.load_workbook(
        filename=path, read_only=True, data_only=True, keep_links=False,
    )
    try:
        sheets = list(workbook.worksheets)
        if len(sheets) > limits.max_sheets:
            raise DocumentLimitExceeded(f"workbook exceeds {limits.max_sheets} sheets")
        blocks = [f"# {path.stem}"]
        total_cells = 0
        for sheet in sheets:
            if int(sheet.max_row or 0) > limits.max_rows_per_sheet:
                raise DocumentLimitExceeded(
                    f"sheet {sheet.title!r} exceeds {limits.max_rows_per_sheet} rows"
                )
            rows: list[list[str]] = []
            for index, values in enumerate(sheet.iter_rows(values_only=True), 1):
                if index > limits.max_rows_per_sheet:
                    raise DocumentLimitExceeded(
                        f"sheet {sheet.title!r} exceeds {limits.max_rows_per_sheet} rows"
                    )
                row = [_cell_text(value) for value in values]
                while row and not row[-1]:
                    row.pop()
                if not row:
                    continue
                total_cells += len(row)
                if total_cells > limits.max_total_cells:
                    raise DocumentLimitExceeded(
                        f"workbook exceeds {limits.max_total_cells} populated cells"
                    )
                rows.append(row)
            blocks.extend((f"## {_cell_text(sheet.title)}", _markdown_table(rows) or "(empty sheet)"))
        return ExtractedDocument(_bounded("\n\n".join(blocks), limits), "openpyxl")
    finally:
        workbook.close()


def _extract_docx(path: Path, docx_module, limits: DocumentLimits) -> ExtractedDocument:
    document = docx_module.Document(path)
    blocks = [f"# {path.stem}"]
    total_cells = 0
    for block in document.iter_inner_content():
        if hasattr(block, "rows"):
            rows: list[list[str]] = []
            for row in block.rows:
                values = [_cell_text(cell.text) for cell in row.cells]
                total_cells += len(values)
                if total_cells > limits.max_total_cells:
                    raise DocumentLimitExceeded(
                        f"document exceeds {limits.max_total_cells} table cells"
                    )
                rows.append(values)
            table = _markdown_table(rows)
            if table:
                blocks.append(table)
        else:
            text = _cell_text(getattr(block, "text", ""))
            if text:
                blocks.append(text)
        _bounded("\n\n".join(blocks), limits)
    return ExtractedDocument(_bounded("\n\n".join(blocks), limits), "python-docx")


def _extract_pdf(path: Path, pymupdf_module, limits: DocumentLimits) -> ExtractedDocument:
    document = pymupdf_module.open(path)
    try:
        if bool(document.needs_pass):
            raise DocumentEncrypted("password-protected PDF is not analyzed")
        page_count = int(document.page_count)
        if page_count <= 0:
            raise DocumentMalformed("PDF contains no pages")
        if page_count > limits.max_pages:
            raise DocumentLimitExceeded(f"PDF exceeds {limits.max_pages} pages")
        blocks: list[str] = []
        blank_pages = 0
        pages = None if hasattr(document, "load_page") else list(document.__iter__())
        for index in range(page_count):
            page = document.load_page(index) if pages is None else pages[index]
            text = str(page.get_text("text", sort=True)).strip()
            if text:
                blocks.extend((f"## Page {index + 1}", text))
            else:
                blank_pages += 1
            _bounded("\n\n".join(blocks), limits)
        markdown = _bounded("\n\n".join(blocks), limits)
        return ExtractedDocument(
            markdown,
            "pymupdf",
            page_count=page_count,
            requires_ocr=blank_pages > 0,
        )
    finally:
        document.close()


def _extract_hwp(path: Path, hwpxkit_module, limits: DocumentLimits) -> ExtractedDocument:
    document = hwpxkit_module.parse_file(str(path))
    if hasattr(document, "to_markdown"):
        body = str(document.to_markdown())
    elif hasattr(document, "get_text"):
        body = str(document.get_text())
    else:
        raise DocumentMalformed("hwpxkit document exposes neither to_markdown nor get_text")
    return ExtractedDocument(_bounded(f"# {path.stem}\n\n{body}", limits), "hwpxkit")


def _extract_ocr(path: Path, paddleocr_module, limits: DocumentLimits) -> ExtractedDocument:
    # v1.5 is the validated contract for this feature.  `use_queues=False`
    # keeps all model-owned workers inside the killable adapter process.
    pipeline = paddleocr_module.PaddleOCRVL(pipeline_version="v1.5", use_queues=False)
    with tempfile.TemporaryDirectory(prefix="robo-ocr-") as temp:
        output_dir = Path(temp)
        results = list(pipeline.predict(input=str(path)))
        if not results:
            raise DocumentMalformed("PaddleOCR-VL produced no page results")
        if len(results) > limits.max_pages:
            raise DocumentLimitExceeded(f"OCR output exceeds {limits.max_pages} pages")
        pages: list[str] = []
        for index, result in enumerate(results, 1):
            page_dir = output_dir / f"page-{index:04d}"
            page_dir.mkdir()
            result.save_to_markdown(save_path=page_dir)
            files = sorted(page_dir.rglob("*.md"))
            if not files:
                raise DocumentMalformed("PaddleOCR-VL produced no Markdown result")
            page_text = "\n\n".join(file.read_text(encoding="utf-8") for file in files).strip()
            pages.extend((f"## Page {index}", page_text))
            _bounded("\n\n".join(pages), limits)
        return ExtractedDocument(
            _bounded("\n\n".join(pages), limits),
            "PaddleOCR-VL-1.5",
            page_count=len(results),
            used_ocr=True,
        )


def _load_and_extract(path: Path, extension: str, limits: DocumentLimits) -> ExtractedDocument:
    suffix = extension.lower()
    if suffix in _PDF_SUFFIXES:
        result = _extract_pdf(path, importlib.import_module("pymupdf"), limits)
        if result.requires_ocr:
            ocr_status = adapter_status(".png")
            if ocr_status.state != "available":
                raise OptionalDependencyMissing(
                    f"image-only PDF requires optional dependency: {ocr_status.dependency}"
                )
            return _extract_ocr(path, importlib.import_module("paddleocr"), limits)
        return result
    if suffix in _XLSX_SUFFIXES:
        importlib.import_module("defusedxml")
        return _extract_xlsx(path, importlib.import_module("openpyxl"), limits)
    if suffix in _DOCX_SUFFIXES:
        return _extract_docx(path, importlib.import_module("docx"), limits)
    if suffix in _HWP_SUFFIXES:
        return _extract_hwp(path, importlib.import_module("hwpxkit"), limits)
    if suffix in _IMAGE_SUFFIXES:
        return _extract_ocr(path, importlib.import_module("paddleocr"), limits)
    raise DocumentMalformed(f"unsupported document extension: {extension}")


_ERROR_TYPES = {
    cls.__name__: cls for cls in (
        OptionalDependencyMissing,
        DocumentLimitExceeded,
        DocumentEncrypted,
        DocumentTimeout,
        DocumentMalformed,
    )
}


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process, timeout_seconds: float,
) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as error:
        process.kill()
        await process.wait()
        raise DocumentTimeout(f"document adapter exceeded {timeout_seconds:g} seconds") from error
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise


async def extract_document(
    path: Path,
    extension: str,
    *,
    limits: DocumentLimits | None = None,
) -> ExtractedDocument:
    limits = limits or DocumentLimits()
    path = Path(path)
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        raise DocumentLimitExceeded(
            f"document size {size} exceeds {limits.max_file_bytes} bytes"
        )
    status = adapter_status(extension)
    if status.state == "unsupported":
        raise DocumentMalformed(status.reason)
    if status.state == "missing":
        raise OptionalDependencyMissing(status.reason)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        __name__,
        "--worker",
        str(path),
        extension,
        json.dumps(asdict(limits), separators=(",", ":")),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await _communicate_with_timeout(process, limits.timeout_seconds)
    if len(stdout) > limits.max_output_chars * 4 + 4096:
        raise DocumentLimitExceeded("document adapter protocol output exceeded bound")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DocumentMalformed("document adapter returned an invalid protocol response") from error
    if not payload.get("ok"):
        error_type = _ERROR_TYPES.get(payload.get("error_type"), DocumentMalformed)
        raise error_type(str(payload.get("message") or "document adapter failed"))
    return ExtractedDocument(**payload["document"])


def _worker(path: str, extension: str, limits_json: str) -> int:
    try:
        limits = DocumentLimits(**json.loads(limits_json))
        # Third-party libraries may print progress on stdout.  Keep stdout a
        # closed JSON protocol; dependency chatter is discarded with stderr by
        # the parent process.
        with redirect_stdout(sys.stderr):
            document = _load_and_extract(Path(path), extension, limits)
        payload = {"ok": True, "document": asdict(document)}
    except Exception as error:
        payload = {
            "ok": False,
            "error_type": type(error).__name__,
            "message": str(error)[:500],
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("path")
    parser.add_argument("extension")
    parser.add_argument("limits")
    args = parser.parse_args()
    if not args.worker:
        return 2
    return _worker(args.path, args.extension, args.limits)


if __name__ == "__main__":
    raise SystemExit(_main())
