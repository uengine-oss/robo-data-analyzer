"""Framework/DBMS 공통 테이블 프롬프트 JSON 계약 테스트."""
from __future__ import annotations

import json
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.models.data_object_usage import (
    DatabaseLinkComponentEvidence,
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
    dump_data_object_usages,
    exact_direct_table_ids,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.semantic_tasks.table.prompt_values import (
    build_table_usage_meaning_batch_values,
)
from pipeline.semantic_tasks.batch_planner import Batch
from pipeline.semantic_tasks.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.semantic_tasks.three_split_llm_fanout import (
    _table_usage_meaning_values,
)
from shared.files.cache import SourceFileCache
import unittest
from tests.unittest_function_cases import load_function_cases


def test_framework_table_packet_contains_only_nonduplicated_model_inputs() -> None:
    node = CodeNode(
        id="code:orders.c:load",
        signature="void load(void)",
        file_path="orders.c",
        start_line=10,
        end_line=12,
        label="FUNCTION",
        name="load",
    )
    context = AnalysisContext(
        id=node.id,
        signature=node.signature,
        file_path=node.file_path,
        start_line=10,
        end_line=12,
        direct_reads=("db:orders",),
        data_object_usages=(DataObjectUsageEvidence(
            source_id=node.id,
            raw_reference="ORDERS",
            name="ORDERS",
            access="READ",
            evidence_file=node.file_path,
            evidence_line=11,
            table_id="db:orders",
            resolution="exact",
            qualified_columns=(QualifiedColumnUsage(
                raw_reference="O.STATUS",
                qualifier="O",
                name="STATUS",
                evidence_line=11,
            ),),
        ),),
    )
    context.extra["ast_statements"] = ()
    context.extra["table_op"] = {"db:orders": "SELECT"}
    profile, specs = build_code_semantic_plan(node, context)
    usage_specs = semantic_concern_slots(specs)["table_usage_meanings"]

    values = _table_usage_meaning_values(
        profile,
        usage_specs,
        ("db:orders",),
        (),
        {
            "db:orders": {
                "description": "주문",
                "columns": ["order_id", "status", "unused_note"],
                "sample_rows": [{"order_id": 1}],
            },
        },
        {
            "numbered_source_code": "10 | void load(void) {\n11 | SELECT ...\n12 | }",
            "column_usage_candidates": {"must_not_be_sent": True},
        },
    )

    assert set(values) == {
        "target_id",
        "assigned_table_usages",
        "code_body",
    }
    assignments = json.loads(values["assigned_table_usages"])
    assert assignments["FT1"]["table_description"] == "주문"
    packet = "\n".join(values.values())
    assert "must_not_be_sent" not in packet
    assert "unused_note" not in packet
    assert "sample_rows" not in packet
    assert "direct_reads" not in packet
    assert "direct_writes" not in packet


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
            data_object_usages=(DataObjectUsageEvidence(
                source_id=node.id, raw_reference="RDD01DD_TB", name="RDD01DD_TB",
                alias="B", access="READ", evidence_file=str(source), evidence_line=1,
                table_id="db:RWIS.RDD01DD_TB", resolution="exact",
                qualified_columns=(QualifiedColumnUsage(
                    raw_reference="B.VAL", qualifier="B", name="VAL", evidence_line=1,
                ),),
            ),),
        )
        context.extra["ast_statements"] = ()
        context.extra["column_usage_candidates"] = {
            "db:RWIS.RDD01DD_TB": {
                "VAL": {
                    "ownership": "qualified",
                    "evidence_lines": [1],
                    "source_lines": [{"line": 1, "text": "SELECT B.VAL FROM RDD01DD_TB B;"}],
                },
            },
        }

        profile, specs = build_code_semantic_plan(node, context)
        context.extra["_semantic_frame_plan"] = (
            profile,
            specs,
            semantic_concern_slots(specs),
        )

        usage_values = build_table_usage_meaning_batch_values(
            batch=Batch(None, (node.id,)),
            contexts={node.id: context},
            table_descriptions={"db:RWIS.RDD01DD_TB": {}},
            cache=SourceFileCache(),
        )
        packets = json.loads(usage_values["table_usage_packets"])
        assert set(packets) == {"N1"}
        assert set(packets["N1"]) == {
            "assigned_table_usages",
            "numbered_source",
        }
        assignment = packets["N1"]["assigned_table_usages"]["FT1"]
        assert "table_description" not in assignment
        assert assignment["table"] == "RWIS.RDD01DD_TB"
        assert set(usage_values) == {"target_id", "table_usage_packets"}
        serialized_packet = usage_values["table_usage_packets"]
        assert "node_key" not in serialized_packet
        assert "column_usage_candidates" not in serialized_packet
        assert "referenced_symbol" not in serialized_packet
        assert "external_context" not in serialized_packet
        assert "CONTROL_FLOW_INDEX" not in serialized_packet


def test_unresolved_occurrence_is_not_scheduled_as_exact_table_usage() -> None:
    context = AnalysisContext(
        id="select:1", signature="SELECT", file_path="p.sql",
        start_line=1, end_line=1,
        direct_reads=("db:shop.PUBLIC.ORDERS",),
        data_object_usages=(
            DataObjectUsageEvidence(
                source_id="select:1", raw_reference="ORDERS", name="ORDERS",
                access="READ", evidence_file="p.sql", evidence_line=1,
                table_id="db:shop.PUBLIC.ORDERS", resolution="exact",
            ),
            DataObjectUsageEvidence(
                source_id="select:1", raw_reference="REMOTE_T@LINK", name="REMOTE_T",
                database_link="LINK", database_link_components=(
                    DatabaseLinkComponentEvidence(role="database", name="LINK"),
                ), access="READ", evidence_file="p.sql", evidence_line=1,
            ),
        ),
    )

    assert exact_direct_table_ids(context, "READ") == ("db:shop.PUBLIC.ORDERS",)


def test_direct_table_id_without_exact_usage_evidence_is_not_trusted() -> None:
    context = AnalysisContext(
        id="function:1",
        signature="load()",
        file_path="load.c",
        start_line=1,
        end_line=3,
        direct_reads=("db:ORDERS",),
    )

    assert exact_direct_table_ids(context, "READ") == ()


def test_framework_table_prompt_uses_its_nonduplicated_projection() -> None:
    framework = (_ROOT / "pipeline/semantic_tasks/three_split_llm_fanout.py").read_text(encoding="utf-8")
    dbms = (
        _ROOT / "pipeline/semantic_tasks/table/prompt_values.py"
    ).read_text(encoding="utf-8")
    assert "table_descriptions," in framework
    assert '"direct_reads"' not in framework
    assert '"direct_writes"' not in framework
    assert '"data_object_usages"' not in framework
    assert "table_descriptions," in dbms
    assert '"direct_reads_json"' not in dbms
    assert '"direct_writes_json"' not in dbms
    assert '"direct_data_object_usages_json"' not in dbms


if __name__ == "__main__":
    test_framework_table_prompt_uses_its_nonduplicated_projection()
    print("[OK] table prompt context passed")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
