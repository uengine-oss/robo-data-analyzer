"""TABLE/COLUMN prose is a bounded semantic-frame projection."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from graph.schema.graph_edges import HasColumn, ReadsWrites
from graph.schema.graph_nodes import Column, Table
from tests.pipeline.semantic_prompt_paths import contract_path, load_semantic_rule
from pipeline.semantic_tasks.table.description_frame import (
    build_table_description_plans,
    normalize_table_description_frame_response,
    table_description_prompt_values,
    table_description_frame_response_model,
)
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _collect_column_candidates,
    _direct_operations_by_table,
    _focus_column_candidates,
    _replace_by_id,
    _source_columns_by_table,
    _source_table_evidence,
    _table_target_ids,
    _data_object_meaning_update,
    _missing_usage_context_update,
    _require_exact_update_population,
)
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir


ROOT = Path(__file__).resolve().parents[3]


def _evidence() -> dict:
    return {
        "table": {
            "db": "db", "schema": "public", "name": "orders",
            "source_logical_name": "주문", "source_description": "주문 원장",
            "source_comment": "",
        },
        "direct_operations": [{
            "source_id": "code:orders.load", "relation": "READS",
            "dml_type": "SELECT", "evidence_file": "orders.sql", "evidence_line": 42,
        }],
        "table_usages": [{
            "source_function_id": "code:orders.load",
            "access_mode": "read",
            "usage_summary": "주문 상태를 조회한다.",
        }],
        "columns": [
            {
                "name": "order_id", "source_logical_name": "주문 식별자",
                "source_description": "주문을 구별한다", "source_comment": "",
                "candidates": [{
                    "evidence_lines": [42], "ownership": "sole_direct_table",
                    "source_lines": [{"line": 42, "text": "SELECT order_id"}],
                }],
            },
            {
                "name": "status_cd", "source_logical_name": "",
                "source_description": "", "source_comment": "",
                "candidates": [{
                    "evidence_lines": [43], "ownership": "qualified_column",
                    "source_lines": [{"line": 43, "text": "WHERE status_cd = 'P'"}],
                }],
            },
        ],
    }


def _wire(evidence: dict, *, unknown_column: bool = False) -> tuple[object, object, dict]:
    table_plan, column_plans = build_table_description_plans("db:public.orders", evidence)
    model = table_description_frame_response_model(column_plans)

    payload = {
        "logical_name": "주문",
        "description": "주문 원장",
        "columns": {
            "order_id": {
                "logical_name": "주문 식별자",
                "description": "주문을 구별한다",
            },
            "status_cd": {"logical_name": "", "description": ""},
        },
    }
    if unknown_column:
        payload["columns"]["unknown"] = payload["columns"].pop("status_cd")
    return model, (table_plan, column_plans), payload


class TableSemanticGroundingTest(unittest.TestCase):
    def test_dynamic_wire_requires_exact_table_and_column_slots(self):
        evidence = _evidence()
        model, (table_plan, column_plans), payload = _wire(evidence)
        parsed = model.model_validate(payload)
        self.assertEqual(
            model.model_validate(parsed.model_dump(by_alias=True)),
            parsed,
        )
        result = normalize_table_description_frame_response(
            parsed, table_plan, column_plans,
        )

        self.assertEqual("semantic-frame/v1", result.semantic_frame.schema_version)
        self.assertEqual(("db", "public", "orders"), (
            result.db, result.schema_, result.name,
        ))
        self.assertEqual(set(column_plans), set(result.column_semantic_frames))
        self.assertEqual("insufficient", result.columns[1].evidence_status)
        self.assertEqual("", result.columns[1].logical_name)
        self.assertNotIn("semantic_frame", parsed.model_dump())

        missing = dict(payload)
        missing.pop("logical_name")
        with self.assertRaises(ValidationError):
            model.model_validate(missing)
        with self.assertRaises(ValidationError):
            _wire(evidence, unknown_column=True)[0].model_validate(
                _wire(evidence, unknown_column=True)[2]
            )

        identity_echo = {
            **payload,
            "db": "db", "schema": "public", "name": "orders",
        }
        with self.assertRaises(ValidationError):
            model.model_validate(identity_echo)

        internal_field_echo = {
            **payload,
            "columns": dict(payload["columns"]),
        }
        internal_field_echo["columns"]["column_0"] = (
            internal_field_echo["columns"].pop("order_id")
        )
        with self.assertRaises(ValidationError):
            model.model_validate(internal_field_echo)

    def test_product_meaning_keeps_detail_in_content_addressed_artifacts(self):
        evidence = _evidence()
        model, (table_plan, column_plans), payload = _wire(evidence)
        result = normalize_table_description_frame_response(
            model.model_validate(payload), table_plan, column_plans,
        )
        with tempfile.TemporaryDirectory() as temp:
            token = set_run_dir(Path(temp))
            try:
                table_update = _data_object_meaning_update(
                    entity_kind="table",
                    entity_id="db:public.orders",
                    logical_name=result.logical_name,
                    analyzed_description=result.description,
                    meaning_status=result.evidence_status,
                    semantic_frame=result.semantic_frame,
                )
                column = result.columns[1]
                column_update = _data_object_meaning_update(
                    entity_kind="column",
                    entity_id="db:public.orders.status_cd",
                    logical_name=column.logical_name,
                    analyzed_description=column.description,
                    meaning_status=column.evidence_status,
                    semantic_frame=result.column_semantic_frames[column.name],
                )
                unused_update = _missing_usage_context_update(
                    "table", "db:public.unused",
                )
            finally:
                reset_run_dir(token)

            artifacts = list((Path(temp) / "semantic-artifacts").rglob("*.json"))

        self.assertEqual("sufficient", table_update["meaning_status"])
        self.assertEqual(0, table_update["missing_context_count"])
        self.assertNotIn("missing_context_artifact_hash", table_update)
        self.assertEqual("insufficient", column_update["meaning_status"])
        self.assertGreater(column_update["missing_context_count"], 0)
        self.assertEqual("", column_update["logical_name"])
        self.assertEqual("", column_update["analyzed_description"])
        self.assertEqual("insufficient", unused_update["meaning_status"])
        self.assertEqual(1, unused_update["missing_context_count"])
        self.assertGreaterEqual(len(artifacts), 4)

    def test_table_and_column_product_population_is_exact_once(self):
        _require_exact_update_population(
            "table", ["t1", "t2"], [{"id": "t1"}, {"id": "t2"}],
        )
        with self.assertRaisesRegex(ValueError, "not exact-once"):
            _require_exact_update_population(
                "column", ["c1", "c2"], [{"id": "c1"}, {"id": "c1"}],
            )

    def test_profiles_bind_each_column_to_only_its_own_evidence(self):
        table_plan, column_plans = build_table_description_plans(
            "db:public.orders", _evidence(),
        )
        table_fact = next(
            fact for fact in table_plan[0].evidence.values() if fact.kind == "TABLE"
        )
        status_fact = next(
            fact for fact in column_plans["status_cd"][0].evidence.values()
            if fact.kind == "COLUMN"
        )
        self.assertNotIn("usage_meanings", table_fact.attributes)
        self.assertEqual(
            ["WHERE status_cd = 'P'"],
            status_fact.attributes["usage_evidence"],
        )
        self.assertNotIn("order_id", str(status_fact.model_dump()))

    def test_prompt_packet_has_one_copy_of_each_semantic_input(self):
        evidence = _evidence()
        table_plan, column_plans = build_table_description_plans(
            "db:public.orders", evidence,
        )
        values = table_description_prompt_values(
            "db:public.orders", table_plan, column_plans,
        )
        packet = json.loads(values["table_and_column_meaning_inputs"])

        self.assertEqual({"table_fqn", "table_and_column_meaning_inputs"}, set(values))
        self.assertEqual({"table", "columns"}, set(packet))
        self.assertEqual({"order_id", "status_cd"}, set(packet["columns"]))
        serialized = values["table_and_column_meaning_inputs"]
        self.assertNotIn("주문 상태를 조회한다.", serialized)
        self.assertNotIn("code:orders.load", serialized)
        self.assertNotIn("evidence_file", serialized)
        self.assertNotIn('"evidence"', serialized)
        self.assertIn("usage_evidence", serialized)
        self.assertNotIn("direct_usage_sources", serialized)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("direct_operations", serialized)
        self.assertNotIn("usage_refs", serialized)

    def test_column_usage_reaches_final_candidate_with_coordinates_not_prose(self):
        node = type("Node", (), {
            "id": "code:orders.sql:SELECT@4",
            "extra": {
                "function_table_usages": [{
                    "physical_table_id": "db:orders",
                    "access_mode": "read",
                    "function_table_usage_summary": "orders 상태를 조회한다.",
                }],
                "direct_column_usage_evidence_by_table": {
                    "db:orders": {"STATUS": {
                    "ownership": "sole_direct_table",
                    "evidence_lines": [4],
                    "source_lines": [{"line": 4, "text": "WHERE STATUS = 'P'"}],
                }},},
            },
        })()
        columns = _collect_column_candidates([node])
        candidate = columns["db:orders"]["STATUS"][0]
        self.assertEqual(
            "code:orders.sql:SELECT@4", candidate["source_function_id"],
        )
        self.assertEqual([4], candidate["evidence_lines"])
        self.assertNotIn("usage_summary", candidate)
        self.assertNotIn("function_table_usage_summary", str(columns))

    def test_column_evidence_survives_without_table_usage_llm_output(self):
        node = type("Node", (), {
            "id": "code:orders.sql:SELECT@4",
            "extra": {
                "direct_column_usage_evidence_by_table": {
                    "db:orders": {"STATUS": {
                        "ownership": "qualified",
                        "evidence_lines": [4],
                        "source_lines": [{
                            "line": 4,
                            "text": "WHERE STATUS = 'P'",
                        }],
                    }},
                },
            },
        })()

        columns = _collect_column_candidates([node])

        self.assertEqual(
            "WHERE STATUS = 'P'",
            columns["db:orders"]["STATUS"][0]["source_lines"][0]["text"],
        )

    def test_column_focus_masks_sibling_identifiers_but_keeps_literals(self):
        focused = _focus_column_candidates([{
            "source_function_id": "select:9",
            "ownership": "derived_output_unique", "evidence_lines": [9],
            "source_lines": [{
                "line": 9,
                "text": "SUM(A.COM_UNIT * DECODE(COMPUTE, '+', 1, '-', -1)) -- old COMPUTE",
                "source_id": "select:9", "ownership": "derived_output_unique",
            }],
        }], target_name="COM_UNIT", target_quoted=False, allowed_columns=[
            ("COM_UNIT", False), ("COMPUTE", False), ("TAGSN", False),
        ])
        line = focused[0]["source_lines"][0]
        self.assertNotIn("text", line)
        self.assertEqual(
            "SUM(A.[TARGET_COLUMN] * DECODE([OTHER_COLUMN], '+', 1, '-', -1))",
            line["focus_text"],
        )

    def test_column_focus_keeps_quoted_case_sensitive_siblings_separate(self):
        candidate = [{
            "relation": "CODE_NODE_USES_COLUMN", "source_id": "select:10",
            "ownership": "parser_unqualified_identifier", "evidence_lines": [10],
            "source_lines": [{
                "line": 10, "text": 'SELECT "Foo" FROM "T"',
                "source_id": "select:10",
                "ownership": "parser_unqualified_identifier",
            }],
        }]
        identities = [("Foo", True), ("foo", True)]

        upper = _focus_column_candidates(
            candidate,
            target_name="Foo",
            target_quoted=True,
            allowed_columns=identities,
        )
        lower = _focus_column_candidates(
            candidate,
            target_name="foo",
            target_quoted=True,
            allowed_columns=identities,
        )

        self.assertEqual(
            'SELECT "[TARGET_COLUMN]" FROM "T"',
            upper[0]["source_lines"][0]["focus_text"],
        )
        self.assertEqual(
            'SELECT "[OTHER_COLUMN]" FROM "T"',
            lower[0]["source_lines"][0]["focus_text"],
        )

    def test_final_column_ownership_uses_has_column_edges(self):
        first = Column(id="db:orders.order_id", name="order_id", dtype="VARCHAR")
        second = Column(id="db:orders.status_cd", name="status_cd", dtype="VARCHAR")
        got = _source_columns_by_table({"has_column_edges": [
            HasColumn(parent_id="db:orders", child_id=first.id),
            HasColumn(parent_id="db:orders", child_id=second.id),
        ]}, {first.id: first, second.id: second})
        self.assertEqual(["order_id", "status_cd"], [c.name for c in got["db:orders"]])

    def test_only_deterministically_accessed_ddl_table_is_target(self):
        table = Table(
            db="warehouse", schema="public", name="inventory",
            description="현재 재고를 보관한다.", comment="재고 원장",
        )
        self.assertEqual([], _table_target_ids([table], {}))
        self.assertEqual([table.id], _table_target_ids([table], {table.id: [{}]}))
        self.assertEqual("현재 재고를 보관한다.", _source_table_evidence(table)["source_description"])

    def test_direct_rw_evidence_is_deduplicated_and_keeps_absolute_line(self):
        edge = ReadsWrites(
            source_id="function:reserve", target_id="db:warehouse.public.inventory",
            relation="WRITES", dml_type="UPDATE",
            evidence_file="inventory_service.java", evidence_line=42,
        )
        got = _direct_operations_by_table({"reads_writes_edges": [edge, edge]})
        self.assertEqual(1, len(got[edge.target_id]))
        self.assertEqual(42, got[edge.target_id][0]["evidence_line"])

    def test_state_replacement_preserves_unavailable_and_rejects_unknown(self):
        table = Table(db="db", schema="public", name="orders", logical_name="stale")
        replaced = _replace_by_id(
            [table], [{"id": table.id, "logical_name": ""}], "logical_name",
        )
        self.assertEqual("", replaced[0].logical_name)
        self.assertIs(table, _replace_by_id([table], [], "logical_name")[0])
        with self.assertRaisesRegex(ValueError, "unknown IDs"):
            _replace_by_id([table], [{"id": "db:unknown"}], "logical_name")

    def test_table_prompts_use_the_contract_owned_by_their_output(self):
        usage_contract = (
            contract_path("table", "function_table_usage_contract.md")
        ).read_text(encoding="utf-8").strip()
        for directory, name in (
            ("framework", "code_table_usage_meanings"),
            ("dbms", "code_table_usage_meanings_batch"),
        ):
            rule = load_semantic_rule(directory, name)
            with self.subTest(strategy=directory, rule=name):
                self.assertEqual(
                    usage_contract,
                    rule["static_values"]["function_table_usage_contract"],
                )
                self.assertNotIn("`results`", rule["user_template"])
                self.assertIn("FT key exact required map", rule["user_template"])
                self.assertNotIn("structural_profile", rule["user_template"])
                self.assertNotIn("applicable_slots", rule["user_template"])

        table_contract = (
            contract_path("table", "table_description_contract.md")
        ).read_text(
            encoding="utf-8",
        ).strip()
        for directory, name in (
            ("framework", "table_description"),
            ("dbms", "table_description"),
        ):
            rule = load_semantic_rule(directory, name)
            with self.subTest(strategy=directory, rule=name):
                self.assertEqual(
                    table_contract,
                    rule["static_values"]["table_description_contract"],
                )
                self.assertNotIn("semantic_frame_contract", rule["static_values"])
                self.assertNotIn("`slots`", rule["user_template"])
                self.assertIn("`logical_name`", table_contract)
                self.assertIn("`description`", table_contract)
        self.assertIn("잘못", usage_contract)
        self.assertIn("좋음", usage_contract)
        self.assertIn("예:", table_contract)


if __name__ == "__main__":
    unittest.main()
