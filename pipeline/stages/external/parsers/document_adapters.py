"""Complete optional document adapters with a killable subprocess boundary.

Heavy parsers are never imported when Analyzer starts.  The parent process
checks dependency availability, then runs the parser in a child process.  No
file, page, row, cell, or output-size ceiling silently changes the supported
input domain.  Cancellation kills the child.  Every successful adapter emits
complete Markdown so the document hierarchy parser remains the single model
owner.
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


class DocumentEncrypted(DocumentAdapterError):
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


def _normalize_markdown(markdown: str) -> str:
    return markdown.replace("\r\n", "\n").replace("\r", "\n").strip()


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


def _extract_xlsx(path: Path, openpyxl_module) -> ExtractedDocument:
    workbook = openpyxl_module.load_workbook(
        filename=path, read_only=True, data_only=True, keep_links=False,
    )
    try:
        blocks = [f"# {path.stem}"]
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            for values in sheet.iter_rows(values_only=True):
                row = [_cell_text(value) for value in values]
                while row and not row[-1]:
                    row.pop()
                if not row:
                    continue
                rows.append(row)
            blocks.extend((f"## {_cell_text(sheet.title)}", _markdown_table(rows) or "(empty sheet)"))
        return ExtractedDocument(_normalize_markdown("\n\n".join(blocks)), "openpyxl")
    finally:
        workbook.close()


def _extract_docx(path: Path, docx_module) -> ExtractedDocument:
    document = docx_module.Document(path)
    blocks = [f"# {path.stem}"]
    for block in document.iter_inner_content():
        if hasattr(block, "rows"):
            rows: list[list[str]] = []
            for row in block.rows:
                values = [_cell_text(cell.text) for cell in row.cells]
                rows.append(values)
            table = _markdown_table(rows)
            if table:
                blocks.append(table)
        else:
            text = _cell_text(getattr(block, "text", ""))
            if text:
                blocks.append(text)
    return ExtractedDocument(_normalize_markdown("\n\n".join(blocks)), "python-docx")


def _extract_pdf(path: Path, pymupdf_module) -> ExtractedDocument:
    document = pymupdf_module.open(path)
    try:
        if bool(document.needs_pass):
            raise DocumentEncrypted("password-protected PDF is not analyzed")
        page_count = int(document.page_count)
        if page_count <= 0:
            raise DocumentMalformed("PDF contains no pages")
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
        markdown = _normalize_markdown("\n\n".join(blocks))
        return ExtractedDocument(
            markdown,
            "pymupdf",
            page_count=page_count,
            requires_ocr=blank_pages > 0,
        )
    finally:
        document.close()


def _extract_hwp(path: Path, hwpxkit_module) -> ExtractedDocument:
    document = hwpxkit_module.parse_file(str(path))
    if hasattr(document, "to_markdown"):
        body = str(document.to_markdown())
    elif hasattr(document, "get_text"):
        body = str(document.get_text())
    else:
        raise DocumentMalformed("hwpxkit document exposes neither to_markdown nor get_text")
    return ExtractedDocument(_normalize_markdown(f"# {path.stem}\n\n{body}"), "hwpxkit")


def _extract_ocr(path: Path, paddleocr_module) -> ExtractedDocument:
    # v1.5 is the validated contract for this feature.  `use_queues=False`
    # keeps all model-owned workers inside the killable adapter process.
    pipeline = paddleocr_module.PaddleOCRVL(pipeline_version="v1.5", use_queues=False)
    with tempfile.TemporaryDirectory(prefix="robo-ocr-") as temp:
        output_dir = Path(temp)
        results = list(pipeline.predict(input=str(path)))
        if not results:
            raise DocumentMalformed("PaddleOCR-VL produced no page results")
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
        return ExtractedDocument(
            _normalize_markdown("\n\n".join(pages)),
            "PaddleOCR-VL-1.5",
            page_count=len(results),
            used_ocr=True,
        )


def _load_and_extract(path: Path, extension: str) -> ExtractedDocument:
    suffix = extension.lower()
    if suffix in _PDF_SUFFIXES:
        result = _extract_pdf(path, importlib.import_module("pymupdf"))
        if result.requires_ocr:
            ocr_status = adapter_status(".png")
            if ocr_status.state != "available":
                raise OptionalDependencyMissing(
                    f"image-only PDF requires optional dependency: {ocr_status.dependency}"
                )
            return _extract_ocr(path, importlib.import_module("paddleocr"))
        return result
    if suffix in _XLSX_SUFFIXES:
        importlib.import_module("defusedxml")
        return _extract_xlsx(path, importlib.import_module("openpyxl"))
    if suffix in _DOCX_SUFFIXES:
        return _extract_docx(path, importlib.import_module("docx"))
    if suffix in _HWP_SUFFIXES:
        return _extract_hwp(path, importlib.import_module("hwpxkit"))
    if suffix in _IMAGE_SUFFIXES:
        return _extract_ocr(path, importlib.import_module("paddleocr"))
    raise DocumentMalformed(f"unsupported document extension: {extension}")


_ERROR_TYPES = {
    cls.__name__: cls for cls in (
        OptionalDependencyMissing,
        DocumentEncrypted,
        DocumentMalformed,
    )
}


async def _communicate_until_cancelled(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    try:
        return await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise


async def extract_document(
    path: Path,
    extension: str,
    sealed_source_bytes: bytes,
) -> ExtractedDocument:
    path = Path(path)
    status = adapter_status(extension)
    if status.state == "unsupported":
        raise DocumentMalformed(status.reason)
    if status.state == "missing":
        raise OptionalDependencyMissing(status.reason)
    # Third-party adapters require a filesystem path. Materialize the bytes already
    # read and sealed by the run cache; never reopen the mutable project path here.
    with tempfile.TemporaryDirectory(prefix="robo-document-") as temp:
        sealed_path = Path(temp) / path.name
        sealed_path.write_bytes(sealed_source_bytes)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            __name__,
            "--worker",
            str(sealed_path),
            extension,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await _communicate_until_cancelled(process)
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DocumentMalformed("document adapter returned an invalid protocol response") from error
    if not payload.get("ok"):
        error_type = _ERROR_TYPES.get(payload.get("error_type"), DocumentMalformed)
        raise error_type(str(payload.get("message") or "document adapter failed"))
    return ExtractedDocument(**payload["document"])


def _worker(path: str, extension: str) -> int:
    try:
        # Third-party libraries may print progress on stdout.  Keep stdout a
        # closed JSON protocol; dependency chatter is discarded with stderr by
        # the parent process.
        with redirect_stdout(sys.stderr):
            document = _load_and_extract(Path(path), extension)
        payload = {"ok": True, "document": asdict(document)}
    except Exception as error:
        payload = {
            "ok": False,
            "error_type": type(error).__name__,
            "message": str(error),
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("path")
    parser.add_argument("extension")
    args = parser.parse_args()
    if not args.worker:
        return 2
    return _worker(args.path, args.extension)


if __name__ == "__main__":
    raise SystemExit(_main())
