from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_audit_artifact import build_rule_audit_record
from scripts.judge_run import (
    _ast_facts,
    _ddl_table_names,
    _ddl_axes,
    _graph_rule_structure_delta,
    _source_return_lines,
    load_gold_axes,
    load_rule_findings,
    load_rule_obligations,
    load_rule_rows,
    trusted_rule_rows,
)
from tests.pipeline.parser_call_fixtures import parser_call_occurrences


def _record(node_key: str, target_id: str, row_key: str) -> dict:
    evidence = {
        "conditions": {},
        "effects": {"E1": {"op": "ret", "target": None, "value": "x"}},
        "rows": {row_key: {"role": "exit", "when": [], "direct": ["E1"]}},
    }
    return {
        "target_id": "unknown",
        "user_content": (
            f"### [RULE_TARGET {node_key}]\n"
            f"target_id: {target_id}\n"
            f"rule_evidence_json: {json.dumps(evidence, separators=(',', ':'))}"
        ),
        "response": {
            "parsed": {
                "nodes": {
                    node_key: {
                        "rules": {
                            row_key: {
                                "cond": [], "then": ["E1"], "extra": [],
                                "nl": f"{target_id} 값을 반환한다.",
                            },
                        },
                    },
                },
            },
        },
    }


class JudgeRunDbmsIdentityTest(unittest.TestCase):
    def test_graph_rule_structure_delta_detects_missing_extra_and_changed_values(self) -> None:
        expected = {
            ("code:a", "R1"): {
                "structural_condition": 'F("ready")',
                "structural_effects": ['call("go", None, [])'],
            },
            ("code:a", "R2"): {
                "structural_condition": None,
                "structural_effects": [],
            },
        }
        graph = [
            {
                "target_id": "code:a", "anchor_line": 1,
                "structural_condition": 'F("wrong")',
                "structural_effects": ['call("go", None, [])'],
            },
            {
                "target_id": "code:a", "anchor_line": 3,
                "structural_condition": None,
                "structural_effects": [],
            },
        ]

        delta = _graph_rule_structure_delta(graph, expected)

        self.assertEqual([("code:a", "R2")], delta["missing"])
        self.assertEqual([("code:a", "R3")], delta["extra"])
        self.assertEqual([("code:a", "R1")], delta["mismatches"])

    def test_current_deterministic_rule_artifact_replaces_retired_prompt_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            artifact_dir = run_dir / "rule-structures"
            artifact_dir.mkdir()
            target_id = "code:order.c:validate"
            routine = CodeNode(
                id=target_id,
                name="validate",
                signature="validate()",
                file_path="order.c",
                start_line=1,
                end_line=1,
                label="FUNCTION",
            )
            inventory = build_inventory(
                "if (amount > 0) { approve(); }",
                (ControlSubtreeNode(
                    "if", "IF", 1, 1, expression="amount > 0",
                ),),
                "c",
                statements=(),
                call_occurrences=parser_call_occurrences(((1, "approve"),)),
            )
            record = build_rule_audit_record(routine, inventory)
            (artifact_dir / "validate.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )

            self.assertEqual({(target_id, "R1")}, load_rule_obligations(run_dir))
            rows = load_rule_rows(run_dir)
            self.assertEqual(
                'F("amount > 0")',
                rows[(target_id, "R1")]["structural_condition"],
            )
            self.assertEqual(
                ['call("approve", None, [])'],
                rows[(target_id, "R1")]["structural_effects"],
            )

    def test_dbms_row_owner_and_scoped_finding_drive_trusted_graph_expectation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            artifact_dir = run_dir / "rule-structures"
            artifact_dir.mkdir()
            routine_id = "code:p.sql:p@1"
            child_id = "code:p.sql:IF@10"
            routine = CodeNode(
                id=routine_id, name="p", signature="p", file_path="p.sql",
                start_line=1, end_line=10, label="PROCEDURE",
            )
            inventory = build_inventory(
                "if (ready) { raise err; }",
                (ControlSubtreeNode("if", "IF", 1, 1, expression="ready"),),
                "sql",
                statements=(StatementFact("raise", "RAISE", 1, 1),),
            )
            record = build_rule_audit_record(
                routine, inventory, row_owners={"R1": child_id},
            )
            (artifact_dir / "p.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )
            (run_dir / "analysis-findings.jsonl").write_text(
                json.dumps({
                    "concern": "rules", "status": "unverified",
                    "target_id": f"{child_id}::R-1", "coordinates": [1],
                    "reasons": ["manual semantic review"],
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            rows = load_rule_rows(run_dir)
            findings = load_rule_findings(run_dir)

            self.assertEqual({(child_id, "R1")}, load_rule_obligations(run_dir))
            self.assertEqual({(child_id, "R1")}, set(rows))
            self.assertEqual({(child_id, "R1")}, set(findings))
            self.assertEqual({}, trusted_rule_rows(rows, findings))

    def test_historical_audit_without_row_owners_ignores_unscoped_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            artifact_dir = run_dir / "rule-structures"
            artifact_dir.mkdir()
            routine = CodeNode(
                id="code:p.sql:p@1", name="p", signature="p", file_path="p.sql",
                start_line=1, end_line=2, label="PROCEDURE",
            )
            inventory = build_inventory(
                "if (ready) { approve(); }",
                (ControlSubtreeNode("if", "IF", 1, 1, expression="ready"),),
                "sql", call_occurrences=parser_call_occurrences(((1, "approve"),)),
                statements=(),
            )
            record = build_rule_audit_record(routine, inventory)
            record.pop("row_owners")
            (artifact_dir / "p.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8",
            )
            (run_dir / "analysis-findings.jsonl").write_text(
                json.dumps({
                    "concern": "rules", "status": "unverified",
                    "target_id": "R1", "coordinates": [1],
                    "reasons": ["historical unscoped finding"],
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            self.assertEqual({}, load_rule_findings(run_dir))

    def test_ddl_table_scope_handles_quoted_and_schema_qualified_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scope.sql"
            path.write_text(
                'CREATE TABLE "ORDERS" (ID NUMBER);\n'
                'create table APP.order_item (ID NUMBER);\n',
                encoding="utf-8",
            )
            self.assertEqual({"orders", "order_item"}, _ddl_table_names(Path(temp)))
            self.assertEqual(
                ({"orders", "order_item"}, {("orders", "id"), ("order_item", "id")}),
                _ddl_axes(Path(temp)),
            )

    def test_uppercase_sql_return_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fn.sql"
            source.write_text(
                "CREATE FUNCTION fn RETURN CHAR\n"
                "BEGIN\n  RETURN V_FLAG;\nEND;\n",
                encoding="utf-8",
            )
            self.assertEqual({"fn.sql": 1}, _source_return_lines(Path(temp)))

    def test_return_inside_string_literal_is_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fn.c"
            source.write_text(
                'log_event("return value");\n'
                "const char *word = 'return';\n"
                "return RET_OK;\n",
                encoding="utf-8",
            )
            self.assertEqual({"fn.c": 1}, _source_return_lines(Path(temp)))

    def test_dbms_call_ast_is_part_of_independent_call_axis(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tree = {
                "type": "PROCEDURE", "name": "p", "children": [
                    {"type": "CALL", "name": "child_proc", "children": []},
                ],
            }
            (Path(temp) / "p.json").write_text(
                json.dumps(tree), encoding="utf-8",
            )
            self.assertEqual({"child_proc"}, _ast_facts(Path(temp))["p"]["calls"])

    def test_same_row_key_in_distinct_targets_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            prompt_dir = Path(temp) / "llm-prompts"
            prompt_dir.mkdir()
            records = [
                _record("1-2", "code:a.sql:a@1", "R1"),
                _record("1-3", "code:b.sql:b@1", "R1"),
            ]
            path = prompt_dir / "code_rules_examples_batch.jsonl"
            path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in records),
                encoding="utf-8",
            )

            self.assertEqual(2, len(load_rule_obligations(Path(temp))))
            self.assertEqual(2, len(load_rule_rows(Path(temp))))

    def test_strict_function_rw_overrides_consolidated_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payloads = {
                "routines.json": [{"file": "source/order/cart.c", "name": "cart_clear"}],
                "tables.json": [{"name": "CART", "columns": []}],
                "relationships.json": {
                    "CALLS": [],
                    "READS": [{
                        "from": "shopmall:source/order/cart.c:cart_clear@1",
                        "to": "CART",
                    }],
                },
                "functions.json": {
                    "cart_clear": {"file": "cart.c", "reads": [], "writes": ["cart"]},
                },
                "references.json": {},
                "init_by.json": [],
            }
            for name, payload in payloads.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")

            axes = load_gold_axes(root)
            self.assertEqual(set(), axes["READS"])
            self.assertEqual(
                {(('order/cart.c', 'cart_clear'), 'cart')},
                axes["WRITES"],
            )

    def test_routine_quality_facts_work_without_optional_functions_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payloads = {
                "routines.json": [{
                    "file": "source/order/cart.c",
                    "name": "cart_clear",
                    "kind": "FUNCTION",
                    "quality_facts": {"reads": ["cart"], "writes": ["audit_log"]},
                }],
                "tables.json": [
                    {"name": "CART", "columns": []},
                    {"name": "AUDIT_LOG", "columns": []},
                ],
                "relationships.json": {"CALLS": []},
            }
            for name, payload in payloads.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")

            axes = load_gold_axes(root)

            routine = ("order/cart.c", "cart_clear")
            self.assertEqual({(routine, "cart")}, axes["READS"])
            self.assertEqual({(routine, "audit_log")}, axes["WRITES"])


if __name__ == "__main__":
    unittest.main()
