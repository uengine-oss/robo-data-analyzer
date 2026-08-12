from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from pipeline.steps.step3_load_external.load_external_step import load_external_step
from pipeline.steps.step3_load_external.parsers.document_adapters import OptionalDependencyMissing
from pipeline.strategies import DBMS_PIPELINE, FRAMEWORK_PIPELINE
from shared.files.cache import SourceFileCache


class _Pause:
    async def gate(self) -> None:
        return None


class _RecordingClient:
    def __init__(self) -> None:
        self.writes: list[tuple[str, list[dict]]] = []

    async def run_batch_unwind(self, query: str, items: list[dict]) -> None:
        self.writes.append((query, items))

    async def run_read_query(self, query: str, params: dict | None = None) -> list[dict]:
        return [{"id": value} for value in (params or {}).get("ids", [])]


class LoadExternalStepTest(unittest.IsolatedAsyncioTestCase):
    @patch(
        "pipeline.steps.step3_load_external.load_external_step.extract_document",
        side_effect=OptionalDependencyMissing("optional dependency not installed: pymupdf"),
    )
    async def test_optional_document_dependency_is_explicit_unanalyzed_status(self, _extract) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "shop"
            root.mkdir()
            (root / "guide.pdf").write_bytes(b"%PDF-1.7\x00binary")
            result = await load_external_step({}, {"configurable": {
                "codebase": SimpleNamespace(root=root),
                "source_cache": SourceFileCache(),
                "pause": _Pause(),
                "neo_client": None,
            }})
            item = result["project_files"][0]
            self.assertEqual(("document", "unanalyzed", "optional_dependency_missing"), (
                item.category, item.status, item.reason,
            ))
            self.assertEqual([], result["external_chunks"])

    async def test_real_inventory_produces_honest_status_and_disjoint_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "shop"
            root.mkdir()
            (root / "A.java").write_text("class A {}", encoding="utf-8")
            (root / "app.properties").write_bytes("shop.title=쇼핑몰".encode("cp949"))
            (root / "mapper.xml").write_text(
                '<mapper namespace="shop.M"><select id="find">SELECT * FROM ORDERS</select></mapper>',
                encoding="utf-8",
            )
            (root / "README.md").write_text("# 주문\n설명", encoding="utf-8")
            (root / "blob.bin").write_bytes(b"a\x00b")
            neo = _RecordingClient()
            result = await load_external_step({}, {"configurable": {
                "codebase": SimpleNamespace(root=root),
                "source_cache": SourceFileCache(),
                "pause": _Pause(),
                "neo_client": neo,
            }})
            self.assertEqual(
                {"project_files", "external_chunks", "external_edges"},
                set(result),
            )
            by_path = {item.relpath: item for item in result["project_files"]}
            self.assertEqual("registered", by_path["A.java"].status)
            self.assertEqual("analyzed", by_path["app.properties"].status)
            self.assertEqual("artifact_xml", by_path["mapper.xml"].category)
            self.assertEqual("binary_unanalyzed", by_path["blob.bin"].reason)
            all_keys = {key for chunk in result["external_chunks"] for key in chunk.keys}
            self.assertIn("shop.title", all_keys)
            self.assertIn("shop.M.find", all_keys)
            # CP949 원문도 디코딩된 소스의 정확한 슬라이스여야 한다.
            for chunk in result["external_chunks"]:
                self.assertEqual("source_span", chunk.provenance)
                self.assertEqual(chunk.char_end - chunk.char_start, len(chunk.text))
            md_chunks = [c for c in result["external_chunks"] if c.file_path == "README.md"]
            self.assertEqual(["md_section"], [c.semantic_kind for c in md_chunks])
            project_payloads = [
                item
                for query, items in neo.writes
                if "SET n:PROJECT_FILE" in query
                for item in items
            ]
            self.assertEqual(5, len(project_payloads))
            self.assertEqual(
                {item.relpath for item in result["project_files"]},
                {item["relpath"] for item in project_payloads},
            )

    def test_both_strategies_parallelize_only_disjoint_step2_and_step3(self) -> None:
        # Framework legacy discovery는 AST와 독립이라 3-way 병렬이다. DBMS spec126은
        # AST object evidence가 resolver 입력이므로 load_ast/load_external만 병렬이고,
        # resolve_data_objects_step은 그 다음 barrier에서 실행돼야 한다.
        framework_parallel = FRAMEWORK_PIPELINE.steps[1]
        self.assertEqual(
            {"load_ast_step", "load_external_step", "discover_datasource_step"},
            {step.__name__ for step in framework_parallel},
        )
        dbms_parallel = DBMS_PIPELINE.steps[1]
        self.assertEqual(
            {"load_ast_step", "load_external_step"},
            {step.__name__ for step in dbms_parallel},
        )
        self.assertEqual("resolve_data_objects_step", DBMS_PIPELINE.steps[2].__name__)


if __name__ == "__main__":
    unittest.main()
