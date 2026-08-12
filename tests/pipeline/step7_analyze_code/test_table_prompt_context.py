"""Framework/DBMS 공통 테이블 프롬프트 JSON 계약 테스트."""
from __future__ import annotations

import json
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.steps.step7_analyze_code.table_prompt_context import (
    dump_table_refs,
)
from pipeline.models.data_object_usage import (
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
    dump_data_object_usages,
    exact_direct_table_ids,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.steps.step7_analyze_code.batch_prompt_values import (
    _build_batch_values,
    build_rule_batch_values,
    build_table_insight_batch_values,
)
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from shared.files.cache import SourceFileCache
import unittest
from tests.unittest_function_cases import load_function_cases


def test_table_id_is_separate_from_description_and_columns() -> None:
    dumped = dump_table_refs(
        ("db:comm_code",),
        {
            "db:comm_code": {
                "description": "공통코드: 설명에 콜론이 있어도 ID가 아니다",
                "columns": ["code_group", "code_value"],
                "sample_rows": [],
            },
        },
    )
    records = json.loads(dumped)
    assert records == [{
        "table_id": "db:comm_code",
        "description": "공통코드: 설명에 콜론이 있어도 ID가 아니다",
        "columns": ["code_group", "code_value"],
        "sample_rows": [],
    }]
    assert not dumped.startswith("db:comm_code:")


def test_empty_and_multiple_table_sets_are_exact() -> None:
    contexts = {
        "db:orders": {"description": "주문", "columns": ["order_id"]},
        "db:shipping": {"description": "배송", "columns": ["order_id"]},
    }
    assert dump_table_refs((), contexts) == "[]"
    records = json.loads(dump_table_refs(("db:orders", "db:shipping"), contexts))
    assert [record["table_id"] for record in records] == ["db:orders", "db:shipping"]


def test_code_node_prompt_omits_unowned_columns_without_name_inference() -> None:
    context = {
        "db:orders": {
            "description": "주문",
            "columns": ["order_id", "status", "unused_note"],
        }
    }
    dumped = dump_table_refs(
        ("db:orders",),
        context,
        include_columns=False,
    )
    assert json.loads(dumped)[0]["columns"] == []


def test_data_object_usage_keeps_alias_column_ownership_separate_per_table() -> None:
    dumped = dump_data_object_usages((
        DataObjectUsageEvidence(
            source_id="select:239", raw_reference="RDIB2EN_TB", name="RDIB2EN_TB",
            alias="A", access="READ", evidence_file="p.sql", evidence_line=241,
            table_id="db:RWIS.RDIB2EN_TB", resolution="exact",
            qualified_columns=(QualifiedColumnUsage(
                raw_reference="A.BNB_CODE", qualifier="A", name="BNB_CODE", evidence_line=239,
            ),),
        ),
        DataObjectUsageEvidence(
            source_id="select:239", raw_reference="RDD01DD_TB", name="RDD01DD_TB",
            alias="B", access="READ", evidence_file="p.sql", evidence_line=242,
            table_id="db:RWIS.RDD01DD_TB", resolution="exact",
            qualified_columns=(QualifiedColumnUsage(
                raw_reference="B.VAL", qualifier="B", name="VAL", evidence_line=239,
            ),),
        ),
    ))
    records = json.loads(dumped)
    assert records[0]["qualified_columns"][0]["name"] == "BNB_CODE"
    assert records[1]["qualified_columns"][0]["name"] == "VAL"
    assert records[0]["producer"] == "parser_ast"


def test_dbms_batch_prompt_carries_exact_alias_column_evidence() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "p.sql"
        source.write_text("SELECT B.VAL FROM RDD01DD_TB B;\n", encoding="utf-8")
        node = CodeNode(
            id="select:1", signature="SELECT", file_path=str(source),
            start_line=1, end_line=1, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=1, direct_reads=("db:RWIS.RDD01DD_TB",),
        )
        context.extra["ast_statements"] = ()
        context.extra["data_object_usages"] = (DataObjectUsageEvidence(
            source_id=node.id, raw_reference="RDD01DD_TB", name="RDD01DD_TB",
            alias="B", access="READ", evidence_file=str(source), evidence_line=1,
            table_id="db:RWIS.RDD01DD_TB", resolution="exact",
            qualified_columns=(QualifiedColumnUsage(
                raw_reference="B.VAL", qualifier="B", name="VAL", evidence_line=1,
            ),),
        ),)
        context.extra["column_usage_candidates"] = {
            "db:RWIS.RDD01DD_TB": {
                "VAL": {
                    "ownership": "qualified",
                    "evidence_lines": [1],
                    "source_lines": [{"line": 1, "text": "SELECT B.VAL FROM RDD01DD_TB B;"}],
                },
            },
        }

        values, _ = _build_batch_values(
            Batch(None, (node.id,)), {node.id: context}, {node.id: node}, "", {},
            {"db:RWIS.RDD01DD_TB": {}}, SourceFileCache(),
        )

        assert 'direct_data_object_usages_json: [{"relation":"TARGET_ACCESSES_DATA_OBJECT","table_id":"db:RWIS.RDD01DD_TB"' in values["nodes"]
        assert '"raw_reference":"B.VAL","qualifier":"B","name":"VAL"' in values["nodes"]
        assert "column_usage_candidates_json:" not in values["nodes"]

        insight_values = build_table_insight_batch_values(
            batch=Batch(None, (node.id,)),
            contexts={node.id: context},
            code_by_id={node.id: node},
            table_descriptions={"db:RWIS.RDD01DD_TB": {}},
            cache=SourceFileCache(),
        )
        assert "direct_reads_json:" in insight_values["table_usage_nodes"]
        assert "column_usage_candidates_json:" in insight_values["table_usage_nodes"]
        assert '"VAL":{"evidence_lines":[1],"ownership":"qualified"' in insight_values["table_usage_nodes"]
        assert "[CONTROL_FLOW_INDEX]" not in insight_values["table_usage_nodes"]
        assert "callee_interfaces_json" not in insight_values["table_usage_nodes"]


def test_dbms_summary_prompt_projects_false_and_error_paths_explicitly() -> None:
    with TemporaryDirectory() as tmp:
        source = Path(tmp) / "p.sql"
        source.write_text(
            "BEGIN\nIF MATCH_COUNT >= 1 THEN\n  WORK;\nEND IF;\n"
            "EXCEPTION\nWHEN OTHERS THEN\n  LOG_ERROR;\nEND;\n",
            encoding="utf-8",
        )
        node = CodeNode(
            id="procedure:1", signature="P", file_path=str(source),
            start_line=1, end_line=8, label="PROCEDURE",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=8,
        )
        context.extra["control_subtree"] = [
            ControlSubtreeNode(
                "if@2", "IF", 2, 4, expression="MATCH_COUNT >= 1",
            ),
            ControlSubtreeNode("exception@5", "EXCEPTION", 5, 8),
        ]
        context.extra["ast_statements"] = ()

        values, _ = _build_batch_values(
            Batch(None, (node.id,)), {node.id: context}, {node.id: node},
            "", {}, {}, SourceFileCache(),
        )

        assert "[implicit_false]" in values["nodes"]
        assert "[error_only]" in values["nodes"]
        assert "handler 진입 아님" in values["nodes"]
        evidence = context.extra["summary_comparison_evidence"]
        assert "MATCH_COUNT >= 1" in evidence
        assert "[implicit_false]" in evidence


def test_unresolved_object_reference_is_not_scheduled_as_exact_table_insight() -> None:
    context = AnalysisContext(
        id="select:1", signature="SELECT", file_path="p.sql",
        start_line=1, end_line=1,
        direct_reads=("db:shop.PUBLIC.ORDERS", "object-ref:abc"),
    )
    context.extra["data_object_usages"] = (
        DataObjectUsageEvidence(
            source_id=context.id, raw_reference="ORDERS", name="ORDERS",
            access="READ", evidence_file="p.sql", evidence_line=1,
            table_id="db:shop.PUBLIC.ORDERS", resolution="exact",
        ),
        DataObjectUsageEvidence(
            source_id=context.id, raw_reference="REMOTE_T@LINK", name="REMOTE_T",
            database_link="LINK", access="READ", evidence_file="p.sql", evidence_line=1,
        ),
    )

    assert exact_direct_table_ids(context, "READ") == ("db:shop.PUBLIC.ORDERS",)


def test_framework_and_dbms_use_the_same_serializer() -> None:
    framework = (_ROOT / "pipeline/steps/step7_analyze_code/three_split_llm_fanout.py").read_text(encoding="utf-8")
    # DBMS 프롬프트 조립은 batch_prompt_values 로 이사 (2026-07 리팩토링) — 계약 대상 갱신.
    dbms = (_ROOT / "pipeline/steps/step7_analyze_code/batch_prompt_values.py").read_text(encoding="utf-8")
    assert '"direct_reads": dump_table_refs(' in framework
    assert '"direct_writes": dump_table_refs(' in framework
    assert "include_columns=False" in framework
    assert '"table_ops": json.dumps(' in framework
    assert "direct_reads_json:" in dbms
    assert "direct_writes_json:" in dbms
    assert "direct_table_operations_json:" in dbms
    assert "direct_data_object_usages_json:" in dbms

    common_prompt = (_ROOT / "llm/prompts/framework/_common.yaml").read_text(encoding="utf-8")
    assert "linked_table_operations_json:" in common_prompt
    assert "{table_ops}" in common_prompt
    assert "{data_object_usages}" in common_prompt


def test_dbms_rule_prompt_uses_routine_table_scope_for_owned_row() -> None:
    """spec117: 배분 노드가 아니라 분기표 소유 루틴의 R/W가 허용 좌표다."""
    routine_id = "code:p.sql:p@1"
    child_id = f"{routine_id}:ASSIGNMENT@28"
    contexts = {
        routine_id: AnalysisContext(
            id=routine_id, signature="p", file_path="p.sql",
            start_line=1, end_line=40, reads=("db:RDITAG_TB",),
        ),
        child_id: AnalysisContext(
            id=child_id, signature="ASSIGNMENT", file_path="p.sql",
            start_line=28, end_line=28,
        ),
    }
    code_by_id = {
        routine_id: CodeNode(
            id=routine_id, signature="p", file_path="p.sql",
            start_line=1, end_line=40, label="FUNCTION",
        ),
        child_id: CodeNode(
            id=child_id, signature="ASSIGNMENT", file_path="p.sql",
            start_line=28, end_line=28, label="ASSIGNMENT",
            parent_id=routine_id,
        ),
    }
    values = build_rule_batch_values(
        expected={"28-28": child_id},
        assignments={"28-28": (28,)},
        evidences={"28-28": {"conditions": {}, "effects": {}, "rows": {}}},
        owner_ids={"28-28": routine_id},
        contexts=contexts,
        code_by_id=code_by_id,
        table_descriptions={"db:RDITAG_TB": {}},
    )
    assert '"table_id":"db:RDITAG_TB"' in values["rule_nodes"]
    assert values["unit_id"] == routine_id
    assert f"owner_id: {routine_id}" in values["rule_nodes"]


if __name__ == "__main__":
    test_table_id_is_separate_from_description_and_columns()
    test_empty_and_multiple_table_sets_are_exact()
    test_framework_and_dbms_use_the_same_serializer()
    print("[OK] table prompt context: 3/3 passed")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
