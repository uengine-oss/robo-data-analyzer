"""TABLE/COLUMN prose is a bounded semantic-frame projection."""
from __future__ import annotations

from pathlib import Path
import unittest

from pydantic import ValidationError

from graph.schema.graph_edges import HasColumn, ReadsWrites
from graph.schema.graph_nodes import Column, Table
from llm.runners.prompt_rules import _load_rule
from pipeline.models.table_description_frame import (
    build_table_description_plans,
    normalize_table_description_frame_response,
    table_description_frame_response_model,
)
from pipeline.models.semantic_frame import semantic_slot_wire_ids
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _collect_usage_candidates,
    _direct_operations_by_table,
    _focus_column_candidates,
    _replace_by_id,
    _source_columns_by_table,
    _source_table_evidence,
    _table_target_ids,
)


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
        "table_usages": [{"source_id": "code:orders.load", "role": "lookup"}],
        "columns": [
            {
                "name": "order_id", "source_logical_name": "주문 식별자",
                "source_description": "주문을 구별한다", "source_comment": "",
                "candidates": [{"evidence_lines": [42], "ownership": "sole_direct_table"}],
            },
            {
                "name": "status_cd", "source_logical_name": "",
                "source_description": "", "source_comment": "",
                "candidates": [{"evidence_lines": [43], "ownership": "qualified_column"}],
            },
        ],
    }


def _wire(evidence: dict, *, unknown_column: bool = False) -> tuple[object, object, dict]:
    table_plan, column_plans = build_table_description_plans("db:public.orders", evidence)
    model = table_description_frame_response_model(evidence, table_plan, column_plans)

    def leaves(specs, prefix: str, insufficient: bool = False) -> dict:
        return {
            wire_id: {
                "meaning": "" if insufficient else f"{prefix} {spec.role}",
                "status": "insufficient" if insufficient else "sufficient",
                "missing_context": ["업무 정의 근거"] if insufficient else [],
            }
            for wire_id, spec in zip(
                semantic_slot_wire_ids(specs), specs, strict=True,
            )
        }

    payload = {
        "db": "db", "schema": "public", "name": "orders",
        "slots": leaves(table_plan[1], "주문"),
        "columns": [
            {"name": name, "slots": leaves(plan[1], name, name == "status_cd")}
            for name, plan in column_plans.items()
        ],
    }
    if unknown_column:
        payload["columns"][1]["name"] = "unknown"
    return model, (table_plan, column_plans), payload


class TableSemanticGroundingTest(unittest.TestCase):
    def test_dynamic_wire_requires_exact_table_and_column_slots(self):
        evidence = _evidence()
        model, (table_plan, column_plans), payload = _wire(evidence)
        parsed = model.model_validate(payload)
        result = normalize_table_description_frame_response(
            parsed, table_plan, column_plans,
        )

        self.assertEqual("semantic-frame/v1", result.semantic_frame.schema_version)
        self.assertEqual(set(column_plans), set(result.column_semantic_frames))
        self.assertEqual("insufficient", result.columns[1].evidence_status)
        self.assertEqual("", result.columns[1].logical_name)
        self.assertNotIn("semantic_frame", parsed.model_dump())

        missing = {**payload, "slots": dict(payload["slots"])}
        missing["slots"].pop(next(iter(missing["slots"])))
        with self.assertRaises(ValidationError):
            model.model_validate(missing)
        with self.assertRaises(ValidationError):
            _wire(evidence, unknown_column=True)[0].model_validate(
                _wire(evidence, unknown_column=True)[2]
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
        self.assertEqual(["code:orders.load"], table_fact.attributes["usage_refs"])
        self.assertEqual([43], status_fact.attributes["usage_lines"])
        self.assertNotIn("order_id", str(status_fact.model_dump()))

    def test_column_usage_reaches_final_candidate_with_coordinates_not_prose(self):
        node = type("Node", (), {
            "id": "code:orders.sql:SELECT@4",
            "extra": {"table_insights": [{
                "table": "db:orders", "role": "lookup", "usage_summary": "legacy projection",
                "columns": [{
                    "name": "STATUS", "ownership": "sole_direct_table",
                    "evidence_lines": [4],
                    "source_lines": [{"line": 4, "text": "WHERE STATUS = 'P'"}],
                }],
            }]},
        })()
        tables, columns = _collect_usage_candidates([node])
        candidate = columns["db:orders"]["STATUS"][0]
        self.assertEqual("code:orders.sql:SELECT@4", candidate["source_id"])
        self.assertEqual([4], candidate["evidence_lines"])
        self.assertNotIn("usage_summary", candidate)
        self.assertEqual("legacy projection", tables["db:orders"][0]["usage_summary"])

    def test_column_focus_masks_sibling_identifiers_but_keeps_literals(self):
        focused = _focus_column_candidates([{
            "relation": "CODE_NODE_USES_COLUMN", "source_id": "select:9",
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

    def test_all_table_prompts_use_shared_frames_without_legacy_wire_fields(self):
        shared = (ROOT / "llm/prompts/common/semantic_frame_contract.md").read_text(
            encoding="utf-8",
        ).strip()
        for directory, name in (
            ("framework", "code_table_insights"),
            ("dbms", "code_table_insights_batch"),
            ("framework", "table_description"),
            ("dbms", "table_description"),
        ):
            rule = _load_rule(ROOT / "llm/prompts" / directory, name)
            with self.subTest(strategy=directory, rule=name):
                self.assertEqual(shared, rule["static_values"]["semantic_frame_contract"])
                self.assertIn("`slots`", rule["user_template"])
                self.assertNotIn("usage_summary", rule["user_template"])
                self.assertIn("잘못", shared)
                self.assertIn("좋음", shared)


if __name__ == "__main__":
    unittest.main()
