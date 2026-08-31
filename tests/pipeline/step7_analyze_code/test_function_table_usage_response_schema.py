"""The table-usage LLM returns meanings, never structural coordinates or roles."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.function_table_usage_response_schema import (
    batch_function_table_usages_response_model,
    function_table_usage_prompt_assignments,
    function_table_usages_response_model,
    normalize_function_table_usages_response,
)
from pipeline.steps.step7_analyze_code.batch_analyze import _collect_keyed_concern
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.files.cache import SourceFileCache


def _write_plan(source: Path) -> tuple[CodeNode, AnalysisContext, object, tuple]:
    node = CodeNode(
        id="node", name="update", signature="update", file_path=str(source),
        start_line=1, end_line=1, label="UPDATE",
    )
    context = AnalysisContext(
        id=node.id, signature=node.signature, file_path=str(source),
        start_line=1, end_line=1,
        writes=["db:orders"], direct_writes=["db:orders"],
        data_object_usages=(DataObjectUsageEvidence(
            source_id=node.id,
            raw_reference="orders",
            name="orders",
            access="WRITE",
            evidence_file=str(source),
            evidence_line=1,
            table_id="db:orders",
            resolution="exact",
        ),),
        extra={
            "table_op": {"db:orders": "UPDATE"},
            "ast_statements": (),
        },
    )
    profile, specs = build_code_semantic_plan(node, context)
    usage_specs = semantic_concern_slots(specs)["table_usage_meanings"]
    context.extra["_semantic_frame_plan"] = (
        profile, specs, semantic_concern_slots(specs),
    )
    return node, context, profile, usage_specs


def _result_payload(model: type) -> dict:
    return {
        key: "orders 상태를 갱신한다."
        for key in model.model_fields
    }


class FunctionTableUsageResponseSchemaTest(unittest.TestCase):
    def test_only_assigned_meaning_results_are_model_output(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            _node, _context, profile, specs = _write_plan(source)
            model = function_table_usages_response_model(
                profile, specs, (), ("db:orders",),
            )
            self.assertEqual(
                {
                    "FT1": {
                        "table": "orders",
                        "access_mode": "write",
                        "occurrences": [{
                            "line": 1,
                            "operation": "UPDATE",
                        }],
                    },
                },
                function_table_usage_prompt_assignments(
                    profile, specs, (), ("db:orders",),
                ),
            )
            payload = _result_payload(model)
            parsed = model.model_validate(payload)
            normalized = normalize_function_table_usages_response(
                parsed, profile, specs, (), ("db:orders",),
            )

        self.assertEqual(1, len(normalized.function_table_usages))
        usage = normalized.function_table_usages[0]
        self.assertEqual("db:orders", usage.physical_table_id)
        self.assertEqual("write", usage.access_mode)
        with self.assertRaises(ValidationError):
            model.model_validate({
                **payload,
                "table_roles": {"db:orders": "WRITE-only"},
            })
        with self.assertRaises(ValidationError):
            model.model_validate({})
        with self.assertRaises(ValidationError):
            model.model_validate({
                **payload,
                "FT999": "허용되지 않은 결과",
            })

    def test_insufficient_is_projected_by_analyzer_without_model_authored_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            _node, _context, profile, specs = _write_plan(source)
            model = function_table_usages_response_model(
                profile, specs, (), ("db:orders",),
            )
            output = model.model_validate({"FT1": ""})
            with patch(
                "pipeline.models.function_table_usage.persist_product_missing_context",
                return_value="a" * 64,
            ) as persist:
                normalized = normalize_function_table_usages_response(
                    output, profile, specs, (), ("db:orders",),
                )

        usage = normalized.function_table_usages[0]
        self.assertEqual("insufficient", usage.meaning_status)
        self.assertIsNone(usage.function_table_usage_summary)
        self.assertEqual(1, usage.missing_context_count)
        self.assertEqual("a" * 64, usage.missing_context_artifact_hash)
        persist.assert_called_once()

    def test_request_local_key_cannot_leak_into_product_meaning(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            _node, _context, profile, specs = _write_plan(source)
            model = function_table_usages_response_model(
                profile, specs, (), ("db:orders",),
            )
            output = model.model_validate({"FT1": "FT1에서 주문을 갱신한다."})
            with self.assertRaises(ValueError):
                normalize_function_table_usages_response(
                    output, profile, specs, (), ("db:orders",),
                )

    def test_batch_requires_every_assigned_node_once(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            _node, _context, profile, specs = _write_plan(source)
            assignments = {
                "N1": {
                    "profile": profile,
                    "slot_specs": specs,
                    "reads": (),
                    "writes": ("db:orders",),
                },
                "N2": {
                    "profile": profile,
                    "slot_specs": specs,
                    "reads": (),
                    "writes": ("db:orders",),
                },
            }
            model = batch_function_table_usages_response_model(assignments)
            item_model = model.model_fields["nodes"].annotation.model_fields[
                "N1"
            ].annotation
            item = _result_payload(item_model)
            model.model_validate({"nodes": {"N1": item, "N2": item}})
            with self.assertRaises(ValidationError):
                model.model_validate({"nodes": {"N1": item}})


class DbmsFunctionTableUsageWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_builds_meaning_only_schema_and_projects_access(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("UPDATE orders SET status = 1;\n", encoding="utf-8")
            node, context, _profile, _specs = _write_plan(source)
            slots = PrioritySemaphore(1)

            async def fake_rule(*, response_model, **_kwargs):
                self.assertEqual(0, slots.free)
                item_type = response_model.model_fields["nodes"].annotation.model_fields[
                    "N1"
                ].annotation
                return response_model.model_validate({
                    "nodes": {
                        "N1": _result_payload(item_type),
                    }
                })

            with patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                side_effect=fake_rule,
            ):
                result = await _collect_keyed_concern(
                    parent_id="parent",
                    node_ids=[node.id],
                    concern="table_usage_meanings",
                    rule_name="code_table_usage_meanings_batch",
                    prompts_dir=Path("."),
                    contexts={node.id: context},
                    code_by_id={node.id: node},
                    cost_budget=None,
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=slots,
                    strategy_name="dbms",
                )

        self.assertEqual(1, len(result[node.id].function_table_usages))
        self.assertEqual(
            "db:orders",
            result[node.id].function_table_usages[0].physical_table_id,
        )


if __name__ == "__main__":
    unittest.main()
