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
    GroundedColumnDescription,
    TableDescription,
    TableInsightItem,
)
from llm.runners.prompt_rules import _load_rule
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _direct_operations_by_table,
    _collect_usage_candidates,
    _focus_column_candidates,
    _source_table_evidence,
    _source_columns_by_table,
    _table_target_ids,
    _table_description_response_model,
    _normalize_table_description_response,
    _replace_by_id,
)
from graph.schema.graph_edges import HasColumn, ReadsWrites
from graph.schema.graph_nodes import Column, Table


def _wire(payload: dict) -> dict:
    semantic_keys = ("logical_name", "description", "evidence_status", "missing_context")
    return {
        "db": payload["db"],
        "schema": payload["schema"],
        "name": payload["name"],
        "meaning": {key: payload[key] for key in semantic_keys},
        "columns": [
            {
                "name": column["name"],
                "meaning": {key: column[key] for key in semantic_keys},
            }
            for column in payload["columns"]
        ],
    }


def _parse(model, payload: dict) -> TableDescription:
    return _normalize_table_description_response(model.model_validate(_wire(payload)))


class TableSemanticGroundingTest(unittest.TestCase):
    def test_table_insight_owns_a_table_specific_usage_summary(self):
        item = TableInsightItem(
            table="db:orders",
            role="lookup",
            usage_summary="주문 상태를 조회해 후속 처리를 결정한다.",
            columns=[],
        )
        self.assertEqual(item.usage_summary, "주문 상태를 조회해 후속 처리를 결정한다.")

    def test_column_usage_reaches_final_candidate_with_full_provenance(self):
        node = type("Node", (), {
            "id": "code:orders.sql:SELECT@4",
            "extra": {"table_insights": [{
                "table": "db:orders",
                "role": "lookup",
                "usage_summary": "처리 대상을 조회한다.",
                "columns": [{
                    "name": "STATUS",
                    "usage_summary": "",
                    "ownership": "sole_direct_table",
                    "evidence_lines": [4],
                    "source_lines": [{"line": 4, "text": "WHERE STATUS = 'P'"}],
                }],
            }]},
        })()

        tables, columns = _collect_usage_candidates([node])

        self.assertEqual("처리 대상을 조회한다.", tables["db:orders"][0]["usage_summary"])
        candidate = columns["db:orders"]["STATUS"][0]
        self.assertEqual(node.id, candidate["source_id"])
        self.assertEqual([4], candidate["evidence_lines"])
        self.assertEqual("WHERE STATUS = 'P'", candidate["source_lines"][0]["text"])
        self.assertNotIn("usage_summary", candidate)

    def test_column_prompt_focus_masks_sibling_identifiers_but_keeps_literals(self):
        focused = _focus_column_candidates(
            [{
                "relation": "CODE_NODE_USES_COLUMN",
                "source_id": "select:9",
                "ownership": "derived_output_unique",
                "evidence_lines": [9],
                "source_lines": [{
                    "line": 9,
                    "text": "SUM(A.COM_UNIT * DECODE(COMPUTE, '+', 1, '-', -1)) -- old COMPUTE",
                    "source_id": "select:9",
                    "ownership": "derived_output_unique",
                }],
            }],
            target_name="COM_UNIT",
            allowed_names=["COM_UNIT", "COMPUTE", "TAGSN"],
        )
        line = focused[0]["source_lines"][0]
        self.assertNotIn("text", line)
        self.assertEqual(
            "SUM(A.[TARGET_COLUMN] * DECODE([OTHER_COLUMN], '+', 1, '-', -1))",
            line["focus_text"],
        )
        self.assertEqual(9, line["line"])

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
        ungrounded = {
            "db": "db",
            "schema": "public",
            "name": "orders",
            "logical_name": "",
            "description": "주문 처리 상태를 보관한다.",
            "evidence_status": "insufficient",
            "missing_context": ["업무 의미를 입증할 코드 또는 주석"],
            "columns": [
                {
                    "name": "order_id",
                    "logical_name": "주문식별자",
                    "description": "주문을 구별하는 값",
                    "evidence_status": "insufficient",
                    "missing_context": ["컬럼 업무 의미 근거"],
                },
                {
                    "name": "status_cd",
                    "logical_name": "처리상태",
                    "description": "주문 처리 상태",
                    "evidence_status": "insufficient",
                    "missing_context": ["컬럼 업무 의미 근거"],
                },
            ],
        }
        unknown = {
            **ungrounded,
            "logical_name": "",
            "description": "",
            "columns": [
                {**column, "logical_name": "", "description": ""}
                for column in ungrounded["columns"]
            ],
        }
        self.assertEqual("", _parse(model, unknown).columns[0].logical_name)
        with self.assertRaises(ValidationError):
            model.model_validate(_wire(ungrounded))
        with self.assertRaises(ValidationError):
            model.model_validate(_wire({**unknown, "columns": unknown["columns"][:1]}))
        with self.assertRaises(ValidationError):
            model.model_validate(_wire({
                **unknown,
                "columns": [unknown["columns"][0], unknown["columns"][0]],
            }))

    def test_column_candidate_does_not_license_table_invention(self):
        model = _table_description_response_model({
            "table": {
                "db": "db", "schema": "public", "name": "orders",
                "source_logical_name": "", "source_description": "", "source_comment": "",
            },
            "columns": [{
                "name": "status_cd", "source_logical_name": "",
                "source_description": "", "source_comment": "",
                "candidates": [{"logical_name": "처리상태", "description": "주문 처리 상태"}],
            }],
        })
        parsed = _parse(model, {
            "db": "db", "schema": "public", "name": "orders",
            "logical_name": "", "description": "",
            "evidence_status": "insufficient",
            "missing_context": ["테이블 역할을 입증할 직접 사용 또는 설명"],
            "columns": [{
                "name": "status_cd", "logical_name": "처리상태",
                "description": "주문 처리 상태",
                "evidence_status": "sufficient", "missing_context": [],
            }],
        })
        self.assertEqual("처리상태", parsed.columns[0].logical_name)

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

    def test_only_deterministically_accessed_ddl_table_is_target(self):
        table = Table(
            db="warehouse",
            schema="public",
            name="inventory",
            description="현재 재고를 보관한다.",
            comment="재고 원장",
        )
        self.assertEqual([], _table_target_ids([table], {}))
        self.assertEqual([table.id], _table_target_ids([table], {table.id: [{}]}))
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
        accessed = {tables[1].id: [{}], tables[3].id: [{}]}
        self.assertEqual([tables[1].id, tables[3].id], _table_target_ids(tables, accessed))

    def test_provenance_comment_is_not_semantic_evidence(self):
        model = _table_description_response_model({
            "table": {
                "db": "db", "schema": "", "name": "orders",
                "source_logical_name": "", "source_description": "",
                "source_comment": "Source references: ORDERS Operations: READ",
            },
            "columns": [{
                "name": "status_cd", "source_logical_name": "",
                "source_description": "", "source_comment": "AMBIGUOUS_SOURCE",
            }],
        })
        parsed = _parse(model, {
            "db": "db", "schema": "", "name": "orders",
            "logical_name": "", "description": "",
            "evidence_status": "insufficient",
            "missing_context": ["테이블 업무 의미를 입증할 직접 사용 또는 설명"],
            "columns": [{
                "name": "status_cd", "logical_name": "", "description": "",
                "evidence_status": "insufficient",
                "missing_context": ["컬럼 의미를 입증할 근거"],
            }],
        })
        self.assertEqual("", parsed.description)
        self.assertEqual("", parsed.columns[0].description)

    def test_usage_evidence_can_honestly_end_as_insufficient(self):
        evidence = {
            "table": {"db": "db", "schema": "", "name": "copy_log"},
            "table_usages": [{"role": "READ-only", "usage_summary": "값을 복사한다"}],
            "columns": [{
                "name": "OPAQUE_VAL",
                "candidates": [{"usage_summary": "다른 행으로 값을 복사한다"}],
            }],
        }
        model = _table_description_response_model(evidence)
        parsed = _parse(model, {
            "db": "db", "schema": "", "name": "copy_log",
            "logical_name": "", "description": "",
            "evidence_status": "insufficient",
            "missing_context": ["테이블의 업무 역할을 설명하는 DDL comment"],
            "columns": [{
                "name": "OPAQUE_VAL", "logical_name": "", "description": "",
                "evidence_status": "insufficient",
                "missing_context": ["복사되는 값의 업무 정의"],
            }],
        })
        self.assertEqual("insufficient", parsed.evidence_status)
        self.assertEqual("insufficient", parsed.columns[0].evidence_status)

        schema = model.model_json_schema()
        self.assertIn("properties", schema, "provider tool conversion needs a root object")
        self.assertEqual(3, len(schema["properties"]["meaning"]["oneOf"]),
                         "table state machine is provider-visible")
        column_ref = schema["properties"]["columns"]["items"]["$ref"]
        column_schema = schema["$defs"][column_ref.rsplit("/", 1)[-1]]
        self.assertEqual(3, len(column_schema["properties"]["meaning"]["oneOf"]),
                         "column state machine is provider-visible")

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
            evidence_status="sufficient",
            missing_context=[],
            columns=[
                GroundedColumnDescription(
                    name="status_cd",
                    logical_name="주문상태",
                    description="주문 처리 상태 코드",
                    evidence_status="sufficient",
                    missing_context=[],
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

    def test_table_insight_passes_column_coordinates_without_generating_meaning(self):
        for directory, rule_name in (
            (_ROOT / "llm/prompts/framework", "code_table_insights"),
            (_ROOT / "llm/prompts/dbms", "code_table_insights_batch"),
        ):
            text = _load_rule(directory, rule_name)["user_template"]
            with self.subTest(rule=rule_name):
                self.assertIn("column_usage_candidates", text)
                self.assertIn("한 번만 생성", text)
                self.assertIn("`columns` 필드를 중복 생성하지", text)

    def test_table_insight_does_not_offer_copyable_generic_good_sentence(self):
        anchor = "처리 대상의 현재 상태를 조회해 갱신 여부"
        for directory, rule_name in (
            (_ROOT / "llm/prompts/framework", "code_table_insights"),
            (_ROOT / "llm/prompts/dbms", "code_table_insights_batch"),
        ):
            text = _load_rule(directory, rule_name)["user_template"]
            with self.subTest(rule=rule_name):
                self.assertNotIn(anchor, text)
                self.assertIn("직접 연산", text)
                self.assertIn("예시 문구", text)

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
            "evidence_status": "sufficient", "missing_context": [],
            "columns": [{
                "name": "drw_bank_ctz_num_pinf",
                "logical_name": "주민 부분정보",
                "description": "출금은행 주민 부분정보 (암호화)",
                "evidence_status": "sufficient", "missing_context": [],
            }],
        }
        parsed = _parse(model, valid)
        self.assertEqual(
            "출금은행 주민 부분정보 (암호화)", parsed.columns[0].description,
        )
        changed = {**valid, "columns": [{
            **valid["columns"][0],
            "description": "출금 은행 계좌 번호의 일부 정보로 저장됩니다.",
        }]}
        with self.assertRaises(ValidationError):
            model.model_validate(_wire(changed))

    def test_step8_does_not_use_function_summary_or_first_column_candidate(self):
        source = (
            _ROOT
            / "pipeline/steps/step8_post_processing/enrich_table_description_step.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"code_summary": summary', source)
        self.assertNotIn("column_descriptions[table].setdefault", source)
        self.assertIn('insight.get("usage_summary")', source)
        self.assertIn("build_consolidated_column_updates", source)
        self.assertIn("table_fqns = _table_target_ids(source_tables, direct_operations)", source)
        self.assertNotIn("run_concern_with_retry(", source)
        self.assertIn('rule_name="table_description"', source)
        self.assertNotIn('reason="no table_insights aggregated"', source)
        self.assertNotIn("ordered_names = list(column_candidates", source)


if __name__ == "__main__":
    unittest.main()
