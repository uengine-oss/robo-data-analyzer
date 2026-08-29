from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


OPTIONAL_MODULES = ("pymupdf", "openpyxl", "docx", "hwpxkit", "paddleocr")
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


class _Sheet:
    title = "주문"
    max_row = 2
    max_column = 2

    def iter_rows(self, *, values_only: bool):
        assert values_only
        return iter((("상품", "수량"), ("연필", 2)))


class _Workbook:
    worksheets = (_Sheet(),)
    closed = False

    def close(self) -> None:
        self.closed = True


class _Paragraph:
    def __init__(self, text: str) -> None:
        self.text = text


class _Cell:
    def __init__(self, text: str) -> None:
        self.text = text


class _Row:
    def __init__(self, *values: str) -> None:
        self.cells = tuple(_Cell(value) for value in values)


class _Table:
    def __init__(self) -> None:
        self.rows = (_Row("상품", "상태"), _Row("연필", "배송"))


class DocumentAdapterTest(unittest.IsolatedAsyncioTestCase):
    def test_import_is_lazy_and_status_names_dependency_and_license(self) -> None:
        before = {name for name in OPTIONAL_MODULES if name in sys.modules}
        module = importlib.import_module(
            "pipeline.steps.step3_load_external.parsers.document_adapters"
        )
        self.assertEqual(before, {name for name in OPTIONAL_MODULES if name in sys.modules})
        pdf = module.adapter_status(".pdf")
        self.assertEqual(("pymupdf", "AGPL-3.0-or-commercial"), (pdf.dependency, pdf.license))
        self.assertIn(pdf.state, {"available", "missing"})
        self.assertEqual(
            "paddlepaddle+paddleocr[doc-parser]",
            module.adapter_status(".png").dependency,
        )
        self.assertEqual("openpyxl+defusedxml", module.adapter_status(".xlsx").dependency)

    def test_xlsx_and_docx_preserve_korean_text_and_table_order(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            _extract_docx,
            _extract_xlsx,
        )

        workbook = _Workbook()
        xlsx = _extract_xlsx(
            Path("orders.xlsx"),
            SimpleNamespace(load_workbook=lambda *args, **kwargs: workbook),
        )
        self.assertEqual(
            "# orders\n\n## 주문\n\n| 상품 | 수량 |\n| --- | --- |\n| 연필 | 2 |",
            xlsx.markdown,
        )
        self.assertTrue(workbook.closed)

        fake_document = SimpleNamespace(
            iter_inner_content=lambda: iter((_Paragraph("주문 안내"), _Table(), _Paragraph("완료")))
        )
        docx = _extract_docx(
            Path("guide.docx"),
            SimpleNamespace(Document=lambda _: fake_document),
        )
        self.assertEqual(
            "# guide\n\n주문 안내\n\n| 상품 | 상태 |\n| --- | --- |\n| 연필 | 배송 |\n\n완료",
            docx.markdown,
        )

    def test_pdf_rejects_encryption_and_routes_image_only_document_to_ocr(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            DocumentEncrypted,
            _extract_pdf,
        )

        encrypted = SimpleNamespace(needs_pass=True, page_count=1, close=lambda: None)
        with self.assertRaises(DocumentEncrypted):
            _extract_pdf(
                Path("locked.pdf"),
                SimpleNamespace(open=lambda _: encrypted),
            )

        class _Page:
            def get_text(self, mode: str, *, sort: bool) -> str:
                self.args = (mode, sort)
                return ""

        image_only = SimpleNamespace(
            needs_pass=False,
            page_count=1,
            load_page=lambda _: _Page(),
            close=lambda: None,
        )
        result = _extract_pdf(
            Path("scan.pdf"),
            SimpleNamespace(open=lambda _: image_only),
        )
        self.assertTrue(result.requires_ocr)
        self.assertEqual("", result.markdown)

    def test_complete_pdf_is_not_rejected_by_page_count(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            _extract_pdf,
        )

        class _Page:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_text(self, mode: str, *, sort: bool) -> str:
                return f"page-{self.number}"

        document = SimpleNamespace(
            needs_pass=False,
            page_count=501,
            load_page=lambda index: _Page(index + 1),
            close=lambda: None,
        )

        result = _extract_pdf(
            Path("complete.pdf"),
            SimpleNamespace(open=lambda _: document),
        )

        self.assertEqual(501, result.page_count)
        self.assertIn("## Page 501\n\npage-501", result.markdown)

    def test_ocr_fixture_preserves_exact_korean_markdown_order_and_hash(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            _extract_ocr,
        )

        expected = (FIXTURES / "korean_ground_truth.md").read_text(encoding="utf-8").strip()
        image_bytes = base64.b64decode(
            (FIXTURES / "korean_image.png.b64").read_text(encoding="ascii")
        )
        self.assertEqual(b"\x89PNG\r\n\x1a\n", image_bytes[:8])

        class _Result:
            def save_to_markdown(self, *, save_path) -> None:
                (Path(save_path) / "result.md").write_text(expected, encoding="utf-8")

        class _Pipeline:
            def predict(self, *, input: str):
                self.input = input
                return [_Result()]

        calls = {}

        def paddle(**kwargs):
            calls.update(kwargs)
            return _Pipeline()

        result = _extract_ocr(
            FIXTURES / "korean_image.png.b64",
            SimpleNamespace(PaddleOCRVL=paddle),
        )
        self.assertEqual({"pipeline_version": "v1.5", "use_queues": False}, calls)
        self.assertEqual(f"## Page 1\n\n{expected}", result.markdown)
        self.assertEqual(
            hashlib.sha256(f"## Page 1\n\n{expected}".encode()).hexdigest(),
            hashlib.sha256(result.markdown.encode()).hexdigest(),
        )

    def test_one_hwp_adapter_owns_hwp_and_hwpx(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            _extract_hwp,
            adapter_status,
        )

        parsed_paths = []
        module = SimpleNamespace(
            parse_file=lambda path: (
                parsed_paths.append(path)
                or SimpleNamespace(to_markdown=lambda: "## 주문\n\n처리 완료")
            )
        )
        result = _extract_hwp(Path("업무.hwpx"), module)
        self.assertEqual("hwpxkit", result.adapter)
        self.assertEqual("# 업무\n\n## 주문\n\n처리 완료", result.markdown)
        self.assertEqual("hwpxkit", adapter_status(".hwp").dependency)
        self.assertEqual("hwpxkit", adapter_status(".hwpx").dependency)
        self.assertEqual(["업무.hwpx"], parsed_paths)

    async def test_cancellation_kills_child_process(self) -> None:
        from pipeline.steps.step3_load_external.parsers.document_adapters import (
            _communicate_until_cancelled,
        )

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        communication = asyncio.create_task(_communicate_until_cancelled(process))
        await asyncio.sleep(0.05)
        communication.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await communication
        self.assertIsNotNone(process.returncode)

    async def test_subprocess_receives_the_sealed_bytes_not_a_reopened_source(self) -> None:
        from pipeline.steps.step3_load_external.parsers import document_adapters as subject

        captured = {}

        class _Process:
            returncode = 0

            async def communicate(self):
                payload = {
                    "ok": True,
                    "document": {
                        "markdown": "sealed",
                        "adapter": "test",
                        "page_count": 0,
                        "requires_ocr": False,
                        "used_ocr": False,
                    },
                }
                return json.dumps(payload).encode("utf-8"), b""

        async def create_process(*args, **_kwargs):
            sealed_path = Path(args[-2])
            captured["name"] = sealed_path.name
            captured["bytes"] = sealed_path.read_bytes()
            return _Process()

        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "orders.xlsx"
            original.write_bytes(b"changed-after-preflight")
            with (
                patch.object(
                    subject,
                    "adapter_status",
                    return_value=subject.AdapterStatus(
                        "test", "test", "test", "available"
                    ),
                ),
                patch.object(
                    subject.asyncio,
                    "create_subprocess_exec",
                    side_effect=create_process,
                ),
            ):
                result = await subject.extract_document(
                    original,
                    ".xlsx",
                    b"sealed-before-mutation",
                )

        self.assertEqual("sealed", result.markdown)
        self.assertEqual("orders.xlsx", captured["name"])
        self.assertEqual(b"sealed-before-mutation", captured["bytes"])


if __name__ == "__main__":
    unittest.main()
