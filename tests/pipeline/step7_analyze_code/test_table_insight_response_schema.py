"""Direct R/W tables are required schema coordinates, not post-hoc filters."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from pipeline.models.table_insight_response_schema import (
    batch_table_insights_response_model,
    normalize_batch_table_insights_response,
    normalize_table_insights_response,
    table_insights_response_model,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step7_analyze_code.batch_analyze import _collect_keyed_concern
from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.files.cache import SourceFileCache


def _item(role: str) -> dict:
    return {
        "role": role,
        "usage_summary": "direct use",
    }


class FrameworkTableInsightSchemaTest(unittest.TestCase):
    def test_required_table_keys_closed_roles_and_canonical_order(self):
        model = table_insights_response_model(
            reads=("db:lookup", "db:both"),
            writes=("db:history", "db:both", "db:merged"),
            table_ops={
                "db:history": "INSERT",
                "db:both": "UPDATE",
                "db:merged": "MERGE",
            },
        )
        payload = {
            "table_insights": {
                "db:lookup": _item("lookup"),
                "db:both": _item("READ_WRITE"),
                "db:history": _item("append-only"),
                "db:merged": _item("upsert"),
            }
        }
        parsed = model.model_validate(payload)
        canonical = normalize_table_insights_response(
            parsed,
            reads=("db:lookup", "db:both"),
            writes=("db:history", "db:both", "db:merged"),
        )
        self.assertEqual(
            ["db:lookup", "db:both", "db:history", "db:merged"],
            [item.table for item in canonical.table_insights],
        )

        invalid = []
        missing = {"table_insights": dict(payload["table_insights"])}
        del missing["table_insights"]["db:lookup"]
        invalid.append(missing)
        extra = {"table_insights": dict(payload["table_insights"])}
        extra["table_insights"]["db:other"] = _item("lookup")
        invalid.append(extra)
        wrong_role = {"table_insights": dict(payload["table_insights"])}
        wrong_role["table_insights"]["db:lookup"] = _item("WRITE-only")
        invalid.append(wrong_role)
        redundant_coordinate = {"table_insights": dict(payload["table_insights"])}
        redundant_coordinate["table_insights"]["db:lookup"] = _item("lookup")
        redundant_coordinate["table_insights"]["db:lookup"]["table"] = "db:lookup"
        invalid.append(redundant_coordinate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                model.model_validate(candidate)

    def test_operation_closes_write_role(self):
        update_model = table_insights_response_model((), ("db:t",), {"db:t": "UPDATE"})
        with self.assertRaises(ValidationError):
            update_model.model_validate({
                "table_insights": {"db:t": _item("append-only")}
            })


class DbmsTableInsightSchemaTest(unittest.TestCase):
    def test_node_and_table_keys_are_both_required(self):
        assignments = {
            "1-3": {
                "reads": ("db:orders",),
                "writes": (),
                "table_ops": {"db:orders": "SELECT"},
            },
            "8-9": {
                "reads": (),
                "writes": ("db:audit",),
                "table_ops": {"db:audit": "INSERT"},
            },
        }
        model = batch_table_insights_response_model(assignments)
        payload = {
            "nodes": {
                "1-3": {
                    "table_insights": {
                        "db:orders": _item("READ-only"),
                    },
                },
                "8-9": {
                    "table_insights": {
                        "db:audit": _item("append-only"),
                    },
                },
            }
        }
        parsed = model.model_validate(payload)
        canonical = normalize_batch_table_insights_response(parsed, assignments)
        self.assertEqual(["1-3", "8-9"], [item.node_key for item in canonical.nodes])
        self.assertEqual(
            [["db:orders"], ["db:audit"]],
            [[table.table for table in item.table_insights] for item in canonical.nodes],
        )

        missing_node = {"nodes": {"1-3": payload["nodes"]["1-3"]}}
        with self.assertRaises(ValidationError):
            model.model_validate(missing_node)


class DbmsTableInsightWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_builds_and_normalizes_fixed_table_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            node = CodeNode(
                id="node", name="update", signature="update", file_path=str(source),
                start_line=1, end_line=1, label="UPDATE",
            )
            context = AnalysisContext(
                id="node", signature="update", file_path=str(source),
                start_line=1, end_line=1,
                writes=["db:orders"],
                extra={"table_op": {"db:orders": "UPDATE"}},
            )
            slots = PrioritySemaphore(1)

            async def fake_rule(*, response_model, **_kwargs):
                self.assertEqual(0, slots.free)
                return response_model.model_validate({
                    "nodes": {
                        "1-1": {
                            "table_insights": {
                                "db:orders": _item("WRITE-only"),
                            },
                        }
                    }
                })

            with (
                patch(
                    "pipeline.steps.step7_analyze_code.batch_analyze._build_batch_values",
                    return_value=({"unit_id": "unit"}, {}),
                ),
                patch(
                    "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                    side_effect=fake_rule,
                ),
            ):
                result = await _collect_keyed_concern(
                    parent_id="parent",
                    node_ids=["node"],
                    concern="insights",
                    rule_name="code_table_insights_batch",
                    response_model=object,
                    prompts_dir=Path("."),
                    contexts={"node": context},
                    code_by_id={"node": node},
                    cost_budget=None,
                    codebase_notes="",
                    module_intents={},
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=slots,
                    strategy_name="dbms",
                )
        self.assertEqual(["db:orders"], [
            item.table for item in result["node"].table_insights
        ])


if __name__ == "__main__":
    unittest.main()
