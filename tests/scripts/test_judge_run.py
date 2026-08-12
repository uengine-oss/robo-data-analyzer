from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.judge_run import (
    VAGUE,
    _ast_facts,
    _ddl_table_names,
    _ddl_axes,
    _delivered_nl_quality,
    _reference_leak_count,
    _source_return_lines,
    load_gold_axes,
    load_rule_obligations,
    load_rule_rows,
)


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

    def test_delivered_graph_narrative_uses_matching_row_evidence(self) -> None:
        evidence = {(
            "code:payment:update_order_paid", "R538",
        ): {
            "when": ["!C471", "!C527"], "direct": ["E1", "E2"],
            "nl": "raw C471 C527 E1 E2",
        }}
        quality = _delivered_nl_quality([{
            "target_id": "code:payment:update_order_paid",
            "anchor_line": 538,
            "nl": "두 결제 조건이 모두 성립하지 않으면 감사 로그를 기록한다.",
        }], evidence)
        self.assertEqual(1, quality["n"])
        self.assertEqual(0, quality["leak"])
        self.assertEqual(0, quality["unmatched"])

    def test_concrete_condition_and_effect_narrative_is_not_vague(self) -> None:
        narrative = "안전 재고 근접 조건이 성립하면 경고 로그와 감사 로그를 기록한다."
        self.assertIsNone(VAGUE.search(narrative))

    def test_business_codes_are_not_misclassified_as_prompt_reference_leaks(self) -> None:
        row = {
            "nl": "현재 상태가 송장(C3)이거나 완료(E5)이면 상태를 확인한다.",
            "when": ["!C140", "C159"],
            "direct": ["E2"],
        }
        self.assertEqual(0, _reference_leak_count(row))

    def test_actual_exposed_reference_ids_are_counted(self) -> None:
        row = {
            "nl": "C159이면 E2를 실행한다.",
            "when": ["!C140", "C159"],
            "direct": ["E2"],
        }
        self.assertEqual(2, _reference_leak_count(row))

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


if __name__ == "__main__":
    unittest.main()
