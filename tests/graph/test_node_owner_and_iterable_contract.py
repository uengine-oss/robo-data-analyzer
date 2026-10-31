"""Analyzer node ownership and one-shot iterable regression tests."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from graph.node_upsert import merge_query
from graph.nodes.module import upsert_modules
from graph.schema.graph_nodes import Module


ROOT = Path(__file__).resolve().parents[2]
GRAPH_DIRECTORY = ROOT / "graph"


class NodeOwnerAndIterableContractTest(unittest.IsolatedAsyncioTestCase):
    def test_node_identity_queries_include_analyzer_owner(self) -> None:
        violations: list[str] = []
        candidates = [GRAPH_DIRECTORY / "node_upsert.py", *sorted((GRAPH_DIRECTORY / "nodes").glob("*.py"))]
        for candidate in candidates:
            source = candidate.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if "{id: item.id}" in stripped and stripped.startswith(("MERGE (", "MATCH (")):
                    violations.append(f"{candidate.name}:{line_number}: {stripped}")

        self.assertEqual(violations, [], "node identity query omitted graph_owner")

    async def test_module_generator_is_reused_for_storage_without_role_label(self) -> None:
        module = Module(
            id="code:orders.c",
            name="orders.c",
            file_path="orders.c",
            label="FILE",
            stereotype="Service",
        )
        graph_database = SimpleNamespace(run_batch_unwind=AsyncMock())

        await upsert_modules(graph_database, (item for item in [module]))

        # 한 번의 MERGE 로 끝난다 — 역할 라벨을 뒤에 붙이는 두 번째 쓰기가 없다.
        self.assertEqual(1, graph_database.run_batch_unwind.await_count)
        query, items = graph_database.run_batch_unwind.await_args.args
        self.assertIn("MERGE (n:ANALYZER_OWNED:FILE {id: item.id", query)
        self.assertEqual("Service", items[0]["stereotype"])

    def test_merge_query_gives_every_label_at_creation(self) -> None:
        query = merge_query("METHOD")
        self.assertIn("MERGE (n:ANALYZER_OWNED:METHOD {id: item.id, graph_owner:", query)
        self.assertNotRegex(query, ADDS_LABEL)


# ``SET n:Label`` 또는 ``SET n.x = 1, n:Label`` — 라벨을 더하는 SET (속성 SET 은 ``n.x``).
ADDS_LABEL = re.compile(r"\bSET\s+[a-z_]+\s*:|\bSET\b.*,\s*[a-z_]+\s*:\s*[`A-Z_{]")


class NoLabelAddedAfterCreationTest(unittest.TestCase):
    """Ontological DB 는 노드의 라벨(타입)을 만들 때 고정한다 — ``SET n:X`` 는 첫 쓰기부터 죽는다.

    그래서 쓰기 경로 어디에도 라벨을 더하는 SET 이 없어야 한다 (속성 SET 은 ``SET n.x``).
    """

    def test_no_write_path_adds_a_label(self) -> None:
        violations: list[str] = []
        for directory in ("graph", "pipeline", "search", "agents", "scripts", "api"):
            for candidate in sorted((ROOT / directory).rglob("*.py")):
                source = candidate.read_text(encoding="utf-8")
                for line_number, line in enumerate(source.splitlines(), start=1):
                    code = re.sub(r"``.*?``", "", line.split("#", 1)[0])
                    if ADDS_LABEL.search(code):
                        violations.append(f"{candidate.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertEqual(violations, [])

    def test_no_query_builds_a_multi_parent_label_chain(self) -> None:
        """``:FUNCTION:EXTERNAL``·``:MODULE:EXTERNAL`` 처럼 한 라벨이 두 부모 밑에 오면 안 된다."""
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((ROOT / "graph").rglob("*.py"))
        )
        self.assertNotIn(":EXTERNAL {", sources)
        self.assertNotIn("FOREACH (", sources)  # 조건부 FOREACH 는 Ontological 이 파싱 못 한다

if __name__ == "__main__":
    unittest.main()
