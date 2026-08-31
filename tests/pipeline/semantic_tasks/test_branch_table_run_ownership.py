# -*- coding: utf-8 -*-
"""분기표 감사 산출물은 활성 분석 run만 소유한다."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ControlSubtreeNode
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir
from pipeline.quality_gates.audit_artifact_paths import audit_file_stem
from pipeline.quality_gates.branch_inventory import build_inventory
from tests.pipeline.parser_call_fixtures import parser_call_occurrences
from pipeline.semantic_tasks.rule.branch_audit import (
    persist_branch_table,
    persist_routine_audits,
)
from pipeline.quality_gates.rule_audit_artifact import (
    RULE_AUDIT_SCHEMA,
    load_rule_audit_records,
    persist_rule_audit,
)


def _node() -> CodeNode:
    return CodeNode(
        id="code:sample.fn", name="fn", signature="fn()", file_path="sample.c",
        start_line=1, end_line=1, label="FUNCTION",
    )


class BranchTableRunOwnership(unittest.TestCase):
    def test_without_active_run_no_directory_is_created(self) -> None:
        with patch("pathlib.Path.mkdir") as mkdir:
            persist_branch_table(_node(), build_inventory("return 0;", (), "c", statements=()))
        mkdir.assert_not_called()

    def test_active_run_receives_branch_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                persist_branch_table(_node(), build_inventory("return 0;", (), "c", statements=()))
            finally:
                reset_run_dir(token)
            output = root / "branch-tables" / f"{audit_file_stem(_node().id)}.json"
            self.assertTrue(output.is_file())
            self.assertIsInstance(json.loads(output.read_text(encoding="utf-8")), list)

    def test_active_run_receives_validated_deterministic_rule_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = build_inventory(
                "if (ready) { ; }",
                (ControlSubtreeNode("if", "IF", 1, 1, expression="ready"),),
                "c",
                statements=(),
            )
            token = set_run_dir(root)
            try:
                persist_rule_audit(_node(), inventory)
            finally:
                reset_run_dir(token)
            records = load_rule_audit_records(root)
            self.assertEqual(["code:sample.fn"], list(records))
            self.assertEqual(RULE_AUDIT_SCHEMA, records["code:sample.fn"]["schema"])
            self.assertEqual(1, len(records["code:sample.fn"]["rules"]))
            self.assertNotIn("nl", records["code:sample.fn"]["rules"][0])

    def test_rule_record_replays_embedded_condition_expression_evidence(self) -> None:
        inventory = build_inventory(
            "if (x == 0) { ; }",
            (ControlSubtreeNode("if", "IF", 1, 1, expression="x == 0"),),
            "c",
            statements=(),
        )
        root_range = {"char_offset": 4, "char_length": 6}
        condition_bundle = {
            "schema": "rule-condition-expression/v1",
            "conditions": {
                "C1": {"kind": "predicate", "expression_refs": ["predicate"]},
            },
            "expressions": {
                "predicate": {
                    "fact_id": "predicate",
                    "source_range": root_range,
                    "source_text": "x == 0",
                    "expression": {
                        "kind": "binary",
                        "source_range": root_range,
                        "operator": "equal",
                        "operands": [
                            {
                                "kind": "reference",
                                "source_range": {"char_offset": 4, "char_length": 1},
                            },
                            {
                                "kind": "literal",
                                "source_range": {"char_offset": 9, "char_length": 1},
                            },
                        ],
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                persist_rule_audit(
                    _node(), inventory, condition_bundle=condition_bundle,
                )
            finally:
                reset_run_dir(token)

            record = load_rule_audit_records(root)[_node().id]
            self.assertEqual(
                "rule-condition-expression/v1",
                record["evidence"]["condition_expression_schema"],
            )
            self.assertEqual(
                'F("x") == F("0")',
                record["rules"][0]["structural_condition"],
            )

    def test_active_run_write_failure_is_not_downgraded_to_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            token = set_run_dir(Path(temp))
            try:
                with (
                    patch("pathlib.Path.write_text", side_effect=PermissionError("denied")),
                    self.assertRaisesRegex(PermissionError, "denied"),
                ):
                    persist_branch_table(
                        _node(), build_inventory("return 0;", (), "c", statements=())
                    )
            finally:
                reset_run_dir(token)

    def test_sanitized_name_collision_cannot_overwrite_audit_records(self) -> None:
        first = CodeNode(
            id="code:a:b", name="a", signature="a()", file_path="a.c",
            start_line=1, end_line=1, label="FUNCTION",
        )
        second = CodeNode(
            id="code:a/b", name="b", signature="b()", file_path="b.c",
            start_line=1, end_line=1, label="FUNCTION",
        )
        inventory = build_inventory("return 0;", (), "c", statements=())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                persist_rule_audit(first, inventory)
                persist_rule_audit(second, inventory)
            finally:
                reset_run_dir(token)
            self.assertNotEqual(audit_file_stem(first.id), audit_file_stem(second.id))
            self.assertEqual(
                {first.id, second.id}, set(load_rule_audit_records(root)),
            )
            self.assertEqual(2, len(list((root / "rule-structures").glob("*.json"))))


class DbmsRoutineAudits(unittest.TestCase):
    """spec 117 — DBMS 도 분기표를 남긴다. 기록 단위는 **루틴당 한 파일**이다.

    종전에는 이 기록이 framework 엔진에만 있어 `judge_run [A]` 가 DBMS 에서 항상 0 이었다
    (제품 결함이 아니라 측정 부재 — 헌법 IX 2층이 성립하지 않던 원인).
    """

    def test_one_file_per_routine_not_per_batch_node(self) -> None:
        routine = CodeNode(
            id="code:p.sql:p@1", name="p", signature="p", file_path="p.sql",
            start_line=1, end_line=3, label="PROCEDURE",
        )
        inventory = build_inventory("return 0;", (), "sql", statements=())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                written = persist_routine_audits(
                    {routine.id: inventory}, {routine.id: routine},
                    row_plan={routine.id: ()},
                )
            finally:
                reset_run_dir(token)
            self.assertEqual([routine.id], written)
            tables = list((root / "branch-tables").glob("*.json"))
            coverage = list((root / "coverage").glob("*.json"))
            rule_structures = list((root / "rule-structures").glob("*.json"))
            self.assertEqual(1, len(tables), "루틴 하나에 파일 하나여야 회계가 맞는다")
            self.assertEqual(1, len(coverage))
            self.assertEqual(1, len(rule_structures))
            self.assertIsInstance(
                json.loads(tables[0].read_text(encoding="utf-8")), list,
            )

    def test_missing_routine_fails_with_coordinates(self) -> None:
        inventory = build_inventory("return 0;", (), "sql", statements=())
        with self.assertRaises(ValueError) as caught:
            persist_routine_audits(
                {"code:missing@1": inventory}, {}, row_plan={},
            )
        self.assertIn("code:missing@1", str(caught.exception))

    def test_dbms_audit_persists_actual_child_owner_per_row(self) -> None:
        routine = CodeNode(
            id="code:p.sql:p@1", name="p", signature="p", file_path="p.sql",
            start_line=1, end_line=3, label="PROCEDURE",
        )
        child = CodeNode(
            id="code:p.sql:IF@1", name="", signature="", file_path="p.sql",
            start_line=1, end_line=2, label="IF", parent_id=routine.id,
        )
        inventory = build_inventory(
            "if (ready) { act(); }",
            (ControlSubtreeNode("if", "IF", 1, 1, expression="ready"),),
            "sql",
            statements=(),
            call_occurrences=parser_call_occurrences(((1, "act"),)),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = set_run_dir(root)
            try:
                persist_routine_audits(
                    {routine.id: inventory},
                    {routine.id: routine, child.id: child},
                    row_plan={child.id: (1,)},
                )
            finally:
                reset_run_dir(token)

            record = load_rule_audit_records(root)[routine.id]
            self.assertEqual({"R1": child.id}, record["row_owners"])


if __name__ == "__main__":
    unittest.main()
