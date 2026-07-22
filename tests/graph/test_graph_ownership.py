import asyncio
from pathlib import Path

from graph.edges.calls import EXTERNAL_QUERY
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.node_upsert import merge_query
from graph.nodes.column import MERGE_QUERY as COLUMN_MERGE_QUERY
from graph.nodes.db_schema import MERGE_QUERY as SCHEMA_MERGE_QUERY
from graph.nodes.example import _UPSERT_QUERY as EXAMPLE_MERGE_QUERY
from graph.nodes.package import MERGE_QUERY as PACKAGE_MERGE_QUERY
from graph.nodes.question import _UPSERT_QUERY as QUESTION_MERGE_QUERY
from graph.nodes.rule import _UPSERT_QUERY as RULE_MERGE_QUERY
from graph.nodes.external import EXTERNAL_NODE_LABELS
from graph.nodes.table import MERGE_QUERY as TABLE_MERGE_QUERY
from graph.schema.constraints import (
    LEGACY_ANALYZER_STABLE_LABELS,
    LEGACY_OWNER_MIGRATION_QUERY,
    wipe_graph,
)
import unittest


ROOT = Path(__file__).resolve().parents[2]


def test_every_node_creation_query_stamps_analyzer_owner() -> None:
    queries = {
        "shared": merge_query("FUNCTION"),
        "schema": SCHEMA_MERGE_QUERY,
        "table": TABLE_MERGE_QUERY,
        "column": COLUMN_MERGE_QUERY,
        "package": PACKAGE_MERGE_QUERY,
        "rule": RULE_MERGE_QUERY,
        "example": EXAMPLE_MERGE_QUERY,
        "question": QUESTION_MERGE_QUERY,
        "external_call_stubs": EXTERNAL_QUERY,
        **{f"external_{label.lower()}": merge_query(label) for label in EXTERNAL_NODE_LABELS},
        "glossary": (ROOT / "pipeline/steps/step8_post_processing/build_glossary_step.py").read_text(encoding="utf-8"),
        "embedding_metadata": (ROOT / "pipeline/steps/step8_post_processing/embed_step.py").read_text(encoding="utf-8"),
    }

    assert ANALYZER_GRAPH_OWNER == "analyzer"
    for writer, query in queries.items():
        assert "graph_owner" in query, f"{writer} writer omitted graph ownership"
        assert (
            "analyzer" in query or "ANALYZER_GRAPH_OWNER" in query
        ), f"{writer} writer used a non-canonical owner"


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    async def run_query(self, query: str, params: dict | None = None):
        self.calls.append((query, params))


async def test_wipe_graph_is_owner_scoped() -> None:
    client = _RecordingClient()
    await wipe_graph(client)  # type: ignore[arg-type]

    assert len(client.calls) == 2
    migration_query, migration_params = client.calls[0]
    assert migration_query == LEGACY_OWNER_MIGRATION_QUERY
    assert migration_params == {
        "graph_owner": ANALYZER_GRAPH_OWNER,
        "stable_labels": list(LEGACY_ANALYZER_STABLE_LABELS),
    }
    assert "label = toUpper(label)" not in migration_query
    assert "file_path IS NOT NULL" in migration_query
    assert "start_line IS NOT NULL" in migration_query
    assert "end_line IS NOT NULL" in migration_query
    assert "UI" not in LEGACY_ANALYZER_STABLE_LABELS
    assert EXTERNAL_NODE_LABELS.issubset(LEGACY_ANALYZER_STABLE_LABELS)

    query, params = client.calls[1]
    assert params == {"graph_owner": ANALYZER_GRAPH_OWNER}
    assert "graph_owner = $graph_owner" in query
    assert query.strip() != "MATCH (n) DETACH DELETE n"
    assert "DataSource" not in query


if __name__ == "__main__":
    test_every_node_creation_query_stamps_analyzer_owner()
    asyncio.run(test_wipe_graph_is_owner_scoped())
    print("[OK] analyzer graph ownership")


# ── 스위트 편입 브리지 (2026-07-22 테스트 정리) ──────────────────────────
# 함수식 케이스는 unittest discover 에 수집되지 않아 스위트에서 실행되지 않는
# 일회성이었다. 아래 브리지가 전 케이스를 스위트 회귀로 편입한다 (재사용 보장).
class ModuleCasesSuiteBridge(unittest.TestCase):
    def test_module_cases(self):
        import asyncio
        import inspect
        cases = [(n, f) for n, f in sorted(globals().items())
                 if n.startswith("test_") and inspect.isfunction(f)]
        self.assertTrue(cases, "함수식 케이스가 없다")
        for name, fn in cases:
            with self.subTest(case=name):
                if inspect.iscoroutinefunction(fn):
                    asyncio.run(fn())
                else:
                    fn()
