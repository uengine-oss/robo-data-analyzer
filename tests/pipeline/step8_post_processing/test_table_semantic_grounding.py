"""테이블·컬럼 의미 통합은 sibling 복사와 첫 후보 승리를 허용하지 않는다."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest
from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.models.code_analysis_output import (
    ColumnInsight,
    TableDescription,
    TableInsightItem,
)
from llm.runners.prompt_rules import _load_rule
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _direct_operations_by_table,
    _source_table_evidence,
    _source_columns_by_table,
    _table_target_ids,
    _table_description_response_model,
    _replace_by_id,
)
from graph.schema.graph_edges import HasColumn, ReadsWrites
from graph.schema.graph_nodes import Column, Table


class TableSemanticGroundingTest(unittest.TestCase):
    def test_table_insight_owns_a_table_specific_usage_summary(self):
        item = TableInsightItem(
            table="db:orders",
            role="lookup",
            usage_summary="주문 상태를 조회해 후속 처리를 결정한다.",
            columns=[],
        )
        self.assertEqual(item.usage_summary, "주문 상태를 조회해 후속 처리를 결정한다.")

    def test_table_role_is_a_closed_contract(self):
        with self.assertRaises(ValidationError):
            TableInsightItem(
                table="db:orders",
                role="read-ish",
                usage_summary="주문을 조회한다.",
                columns=[],
            )

    def test_final_table_schema_requires_every_ddl_column_once(self):
        model = _table_description_response_model({
            "table": {
                "db": "db", "schema": "public", "name": "orders",
                "source_logical_name": "", "source_description": "", "source_comment": "",
            },
            "columns": [
                {"name": "order_id", "source_logical_name": "", "source_description": "", "source_comment": ""},
                {"name": "status_cd", "source_logical_name": "", "source_description": "", "source_comment": ""},
            ],
        })
        valid = {
            "db": "db",
            "schema": "public",
            "name": "orders",
            "logical_name": "",
            "description": "주문 처리 상태를 보관한다.",
            "columns": [
                {
                    "name": "order_id",
                    "logical_name": "주문식별자",
                    "description": "주문을 구별하는 값",
                },
                {
                    "name": "status_cd",
                    "logical_name": "처리상태",
                    "description": "주문 처리 상태",
                },
            ],
        }
        self.assertEqual(2, len(model.model_validate(valid).columns))
        unknown = {
            **valid,
            "logical_name": "",
            "description": "",
            "columns": [
                {**column, "logical_name": "", "description": ""}
                for column in valid["columns"]
            ],
        }
        self.assertEqual("", model.model_validate(unknown).columns[0].logical_name)
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "columns": valid["columns"][:1]})
        with self.assertRaises(ValidationError):
            model.model_validate({
                **valid,
                "columns": [valid["columns"][0], valid["columns"][0]],
            })

    def test_final_column_ownership_uses_has_column_edges(self):
        first = Column(id="db:orders.order_id", name="order_id", dtype="VARCHAR")
        second = Column(id="db:orders.status_cd", name="status_cd", dtype="VARCHAR")
        state = {
            "has_column_edges": [
                HasColumn(parent_id="db:orders", child_id=first.id),
                HasColumn(parent_id="db:orders", child_id=second.id),
            ]
        }
        got = _source_columns_by_table(
            state,
            {first.id: first, second.id: second},
        )
        self.assertEqual(["order_id", "status_cd"], [column.name for column in got["db:orders"]])

    def test_parsed_ddl_table_is_target_even_without_function_insight(self):
        table = Table(
            db="warehouse",
            schema="public",
            name="inventory",
            description="현재 재고를 보관한다.",
            comment="재고 원장",
        )
        self.assertEqual([table.id], _table_target_ids([table]))
        self.assertEqual(
            {
                "db": "warehouse",
                "schema": "public",
                "name": "inventory",
                "source_logical_name": "",
                "source_description": "현재 재고를 보관한다.",
                "source_comment": "재고 원장",
                "table_type": "TABLE",
            },
            _source_table_evidence(table),
        )

    def test_table_target_contract_is_domain_neutral(self):
        tables = [
            Table(db="shop", schema="public", name="orders"),
            Table(db="library", schema="public", name="loans"),
            Table(db="factory", schema="public", name="devices"),
            Table(db="school", schema="public", name="courses"),
        ]
        self.assertEqual([table.id for table in tables], _table_target_ids(tables))

    def test_direct_rw_evidence_is_deduplicated_and_keeps_absolute_line(self):
        edge = ReadsWrites(
            source_id="function:reserve",
            target_id="db:warehouse.public.inventory",
            relation="WRITES",
            dml_type="UPDATE",
            evidence_file="inventory_service.java",
            evidence_line=42,
        )
        got = _direct_operations_by_table({"reads_writes_edges": [edge, edge]})
        self.assertEqual(
            [{
                "source_id": "function:reserve",
                "relation": "WRITES",
                "dml_type": "UPDATE",
                "evidence_file": "inventory_service.java",
                "evidence_line": 42,
            }],
            got[edge.target_id],
        )

    def test_table_description_returns_consolidated_columns(self):
        result = TableDescription(
            db="db",
            schema="public",
            name="orders",
            logical_name="주문 처리 기준",
            description="주문 상태와 처리 기준을 조회하는 데이터.",
            columns=[
                ColumnInsight(
                    name="status_cd",
                    logical_name="주문상태",
                    description="주문 처리 상태 코드",
                )
            ],
        )
        self.assertEqual(result.columns[0].name, "status_cd")

    def test_state_replacement_preserves_unavailable_targets_and_rejects_unknown_ids(self):
        table = Table(
            db="db", schema="public", name="orders",
            logical_name="stale", analyzed_description="stale",
        )
        replaced = _replace_by_id(
            [table],
            [{"id": table.id, "logical_name": "", "analyzed_description": ""}],
            "logical_name", "analyzed_description",
        )
        self.assertEqual("", replaced[0].logical_name)
        self.assertEqual("", replaced[0].analyzed_description)
        untouched = _replace_by_id([table], [], "logical_name", "analyzed_description")
        self.assertIs(table, untouched[0])
        with self.assertRaisesRegex(ValueError, "unknown IDs"):
            _replace_by_id(
                [table],
                [{"id": "db:public.unknown", "logical_name": "", "analyzed_description": ""}],
                "logical_name", "analyzed_description",
            )

    def test_both_strategies_pair_bad_and_good_sibling_examples(self):
        prompts = [
            (_ROOT / "llm/prompts/framework", "code_table_insights"),
            (_ROOT / "llm/prompts/dbms", "code_table_insights_batch"),
            (_ROOT / "llm/prompts/framework", "table_description"),
            (_ROOT / "llm/prompts/dbms", "table_description"),
        ]
        for directory, rule_name in prompts:
            rule = _load_rule(directory, rule_name)
            text = rule["user_template"] + "\n" + "\n".join(rule["static_values"].values())
            with self.subTest(rule=rule_name, strategy=directory.name):
                self.assertIn("usage_summary", text)
                self.assertIn("잘못", text)
                self.assertIn("좋음", text)

    def test_table_insight_forbids_unproven_columns(self):
        for directory, rule_name in (
            (_ROOT / "llm/prompts/framework", "code_table_insights"),
            (_ROOT / "llm/prompts/dbms", "code_table_insights_batch"),
        ):
            text = _load_rule(directory, rule_name)["user_template"]
            with self.subTest(rule=rule_name):
                self.assertIn("컬럼 필드는 출력하지 않는다", text)
                self.assertIn("추측하지 않는다", text)

    def test_column_contract_preserves_source_semantic_head_over_name_guess(self):
        contract = (
            _ROOT / "llm/prompts/common/table_description_contract.md"
        ).read_text(encoding="utf-8")
        # 2026-08-07 계약 개편 — 같은 의미(“source가 구별한 중심 개념을 물리명 추측으로
        # 바꾸지 않는다”)를 실물 입력→출력 예시로 표현한다. 문구 앵커를 새 원문에 맞춘다.
        self.assertIn("중심 명사와 수식어", contract)
        self.assertIn("업무 명사구를 그대로 재사용", contract)
        self.assertIn("고객 일련번호", contract)
        self.assertIn("기관 지점 일련번호", contract)

    def test_source_column_meaning_is_schema_constant_not_posthoc_fallback(self):
        evidence = {
            "table": {
                "db": "db", "schema": "", "name": "auth",
                "source_logical_name": "", "source_description": "인증 이력", "source_comment": "",
            },
            "columns": [{
                "name": "drw_bank_ctz_num_pinf",
                "source_logical_name": "주민 부분정보",
                "source_description": "출금은행 주민 부분정보 (암호화)",
                "source_comment": "",
            }],
        }
        model = _table_description_response_model(evidence)
        valid = {
            "db": "db", "schema": "", "name": "auth",
            "logical_name": "인증 이력",
            "description": "인증 이력",
            "columns": [{
                "name": "drw_bank_ctz_num_pinf",
                "logical_name": "주민 부분정보",
                "description": "출금은행 주민 부분정보 (암호화)",
            }],
        }
        parsed = model.model_validate(valid)
        self.assertEqual(
            "출금은행 주민 부분정보 (암호화)", parsed.columns[0].description,
        )
        changed = {**valid, "columns": [{
            **valid["columns"][0],
            "description": "출금 은행 계좌 번호의 일부 정보로 저장됩니다.",
        }]}
        with self.assertRaises(ValidationError):
            model.model_validate(changed)

    def test_step8_does_not_use_function_summary_or_first_column_candidate(self):
        source = (
            _ROOT
            / "pipeline/steps/step8_post_processing/enrich_table_description_step.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"code_summary": summary', source)
        self.assertNotIn("column_descriptions[table].setdefault", source)
        self.assertIn('insight.get("usage_summary")', source)
        self.assertIn("build_consolidated_column_updates", source)
        self.assertIn("table_fqns = _table_target_ids(source_tables)", source)
        self.assertNotIn("run_concern_with_retry(", source)
        self.assertIn('rule_name="table_description"', source)
        self.assertNotIn('reason="no table_insights aggregated"', source)
        self.assertNotIn("ordered_names = list(column_candidates", source)


if __name__ == "__main__":
    unittest.main()
