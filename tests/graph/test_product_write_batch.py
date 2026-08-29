from __future__ import annotations

import math
import unittest

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    NodePropertySetOperation,
    ProductRecordExpectation,
    ProductWriteBatch,
    ProductWriteContractError,
    RelationshipCreateOperation,
    RelationshipIdentity,
    RelationshipPropertySetOperation,
    canonical_product_record_hash_v2,
    combine_product_write_batches,
    verify_product_write_batch_readback,
    verify_product_write_plan_readback,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260827"


def _expectation(
    identity,
    properties: dict,
    *,
    optional: tuple[str, ...] = (),
) -> ProductRecordExpectation:
    return ProductRecordExpectation.from_properties(
        identity=identity,
        closed_property_keys=tuple(properties) + optional,
        present_properties=properties,
    )


def _create_batch(
    identity: NodeIdentity,
    *,
    stage_name: str = "code_structure",
    extra: dict | None = None,
) -> ProductWriteBatch:
    properties = {
        "stable_id": identity.stable_id,
        "analysis_scope_id": SCOPE,
        "generation_id": GENERATION,
        **(extra or {}),
    }
    operation = NodeCreateOperation.from_properties(identity, properties)
    return ProductWriteBatch.build(
        stage_name=stage_name,
        analysis_scope_id=SCOPE,
        generation_id=GENERATION,
        node_create_operations=[operation],
        expectations=[_expectation(identity, properties)],
    )


class CanonicalProductRecordHashTest(unittest.TestCase):
    def test_same_record_has_same_hash_independent_of_mapping_order(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        first = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=["name", "lines"],
            present_properties={"name": "run", "lines": [1, 2]},
        )
        second = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=["lines", "name"],
            present_properties={"lines": [1, 2], "name": "run"},
        )

        self.assertEqual(
            canonical_product_record_hash_v2(first),
            canonical_product_record_hash_v2(second),
        )

    def test_absent_property_differs_from_present_empty_string(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        absent = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=["summary"],
            present_properties={},
        )
        present = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=["summary"],
            present_properties={"summary": ""},
        )

        self.assertNotEqual(
            canonical_product_record_hash_v2(absent),
            canonical_product_record_hash_v2(present),
        )

    def test_primitive_types_and_list_order_are_hash_significant(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")

        def hashed(value: object) -> str:
            return canonical_product_record_hash_v2(
                ProductRecordExpectation.from_properties(
                    identity=identity,
                    closed_property_keys=["value"],
                    present_properties={"value": value},
                )
            )

        self.assertNotEqual(hashed(True), hashed(1))
        self.assertNotEqual(hashed(1), hashed(1.0))
        self.assertNotEqual(hashed([1, 2]), hashed([2, 1]))

    def test_embedding_hash_includes_model_dimension_and_float_order(self) -> None:
        identity = NodeIdentity("SearchSegment", "segment:1")

        def hashed(model: str, vector: list[float]) -> str:
            properties = {
                "embedding_model_id": model,
                "embedding": vector,
            }
            return canonical_product_record_hash_v2(
                _expectation(identity, properties)
            )

        baseline = hashed("embed-v1", [0.25, -0.5])
        self.assertNotEqual(baseline, hashed("embed-v2", [0.25, -0.5]))
        self.assertNotEqual(baseline, hashed("embed-v1", [0.25]))
        self.assertNotEqual(baseline, hashed("embed-v1", [-0.5, 0.25]))

    def test_neo4j_unsupported_values_are_rejected_before_db_access(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        invalid_values = (
            None,
            {"nested": "map"},
            [[1]],
            [1, "2"],
            math.inf,
            math.nan,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ProductWriteContractError):
                    _expectation(identity, {"value": value})

    def test_embedding_requires_model_id_and_float_vector(self) -> None:
        identity = NodeIdentity("SearchSegment", "segment:1")
        missing_model = _expectation(identity, {"embedding": [0.1]})
        integer_vector = _expectation(
            identity,
            {"embedding_model_id": "embed-v1", "embedding": [1]},
        )

        with self.assertRaises(ProductWriteContractError):
            canonical_product_record_hash_v2(missing_model)
        with self.assertRaises(ProductWriteContractError):
            canonical_product_record_hash_v2(integer_vector)


class ProductWriteBatchTest(unittest.TestCase):
    def test_batch_derives_exact_operation_property_and_hash_ledgers(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        properties = {
            "stable_id": identity.stable_id,
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
            "code_kind": "function",
            "source_lines": [1, 2],
        }
        operation = NodeCreateOperation.from_properties(identity, properties)
        batch = ProductWriteBatch.build(
            stage_name="code_structure",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            node_create_operations=[operation],
            expectations=[_expectation(
                identity,
                properties,
                optional=("qualified_name",),
            )],
        )

        self.assertEqual(1, len(batch.required_operation_key_set))
        self.assertEqual(
            tuple(sorted(properties)),
            dict(batch.required_property_key_set_by_identity)[
                identity.canonical_key()
            ],
        )
        self.assertRegex(
            dict(batch.required_product_record_hash_by_identity)[
                identity.canonical_key()
            ],
            r"^[0-9a-f]{64}$",
        )
        artifact = batch.artifact_dict()
        self.assertEqual(
            batch.canonical_batch_sha256,
            artifact["canonical_batch_sha256"],
        )
        self.assertEqual(
            [1, 2],
            artifact["node_create_operations"][0]["present_properties"][
                "source_lines"
            ],
        )

    def test_operation_and_expectation_must_describe_identical_values(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        operation = NodePropertySetOperation.from_value(
            identity, "summary", "actual",
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "differ from immutable expectation",
        ):
            ProductWriteBatch.build(
                stage_name="function_meanings",
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                node_property_set_operations=[operation],
                expectations=[_expectation(
                    identity, {"summary": "different"},
                )],
            )

    def test_duplicate_property_operation_is_rejected(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        operations = [
            NodePropertySetOperation.from_value(identity, "summary", "value"),
            NodePropertySetOperation.from_value(identity, "summary", "value"),
        ]

        with self.assertRaisesRegex(ProductWriteContractError, "duplicate operation"):
            ProductWriteBatch.build(
                stage_name="function_meanings",
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                node_property_set_operations=operations,
                expectations=[_expectation(identity, {"summary": "value"})],
            )

    def test_node_create_requires_exact_stable_scope_and_generation_properties(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        operation = NodeCreateOperation.from_properties(
            identity,
            {"stable_id": identity.stable_id},
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "identity/control properties",
        ):
            ProductWriteBatch.build(
                stage_name="code_structure",
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                node_create_operations=[operation],
                expectations=[_expectation(
                    identity, {"stable_id": identity.stable_id},
                )],
            )

    def test_identity_and_control_properties_cannot_be_patched(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")

        with self.assertRaisesRegex(
            ProductWriteContractError, "cannot be patched",
        ):
            ProductWriteBatch.build(
                stage_name="function_meanings",
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                node_property_set_operations=[
                    NodePropertySetOperation.from_value(
                        identity, "generation_id", GENERATION,
                    ),
                ],
                expectations=[_expectation(
                    identity, {"generation_id": GENERATION},
                )],
            )

    def test_same_transaction_readback_checks_presence_and_exact_values(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        batch = _create_batch(
            identity,
            extra={"code_kind": "function", "name": "run"},
        )
        expected_properties = dict(
            batch.node_create_operations[0].present_properties
        )

        hashes = verify_product_write_batch_readback(
            batch,
            [(identity, {**expected_properties, "later_stage_property": "allowed"})],
        )
        self.assertEqual(
            batch.required_product_record_hash_by_identity,
            hashes,
        )

        for changed in (
            {key: value for key, value in expected_properties.items() if key != "name"},
            {**expected_properties, "name": "different"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ProductWriteContractError):
                    verify_product_write_batch_readback(
                        batch,
                        [(identity, changed)],
                    )

    def test_readback_population_must_match_the_batch_exactly_once(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        batch = _create_batch(identity)

        with self.assertRaisesRegex(ProductWriteContractError, "identity mismatch"):
            verify_product_write_batch_readback(batch, [])
        with self.assertRaisesRegex(ProductWriteContractError, "duplicate"):
            verify_product_write_batch_readback(
                batch,
                [
                    (identity, dict(batch.node_create_operations[0].present_properties)),
                    (identity, dict(batch.node_create_operations[0].present_properties)),
                ],
            )


class ProductWritePlanTest(unittest.TestCase):
    def test_disjoint_create_and_meaning_patch_form_one_final_record(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        structure = _create_batch(
            identity,
            extra={"code_kind": "function", "name": "run"},
        )
        meaning_properties = {
            "summary": "주문을 검증한다.",
            "stereotype": "PROCESS",
            "meaning_status": "sufficient",
            "missing_context_count": 0,
        }
        meaning = ProductWriteBatch.build(
            stage_name="function_meanings",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            node_property_set_operations=[
                NodePropertySetOperation.from_value(identity, key, value)
                for key, value in meaning_properties.items()
            ],
            expectations=[_expectation(
                identity,
                meaning_properties,
                optional=("missing_context_artifact_hash",),
            )],
        )

        plan = combine_product_write_batches(
            [meaning, structure],
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
        )

        owner_map = {
            (identity_key, key): stage
            for identity_key, key, stage in plan.property_owner_by_identity_and_key
        }
        self.assertEqual(
            "code_structure",
            owner_map[(identity.canonical_key(), "name")],
        )
        self.assertEqual(
            "function_meanings",
            owner_map[(identity.canonical_key(), "summary")],
        )
        self.assertEqual(5, len(plan.required_operation_key_set))
        self.assertEqual(1, len(plan.required_product_record_hash_by_identity))
        self.assertNotIn(
            "missing_context_artifact_hash",
            dict(plan.required_property_key_set_by_identity)[
                identity.canonical_key()
            ],
        )
        self.assertEqual(
            "function_meanings",
            owner_map[(identity.canonical_key(), "missing_context_artifact_hash")],
        )
        complete_properties = {
            **dict(structure.node_create_operations[0].present_properties),
            **meaning_properties,
        }
        self.assertEqual(
            plan.required_product_record_hash_by_identity,
            verify_product_write_plan_readback(
                plan, [(identity, complete_properties)],
            ),
        )
        with self.assertRaisesRegex(
            ProductWriteContractError, "property closure mismatch",
        ):
            verify_product_write_plan_readback(
                plan,
                [(identity, {**complete_properties, "unexpected": "noise"})],
            )

    def test_property_owner_collision_between_stages_is_rejected(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")

        def patch(stage: str, value: str) -> ProductWriteBatch:
            return ProductWriteBatch.build(
                stage_name=stage,
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                node_property_set_operations=[
                    NodePropertySetOperation.from_value(identity, "summary", value),
                ],
                expectations=[_expectation(identity, {"summary": value})],
            )

        with self.assertRaisesRegex(ProductWriteContractError, "property owner conflict"):
            combine_product_write_batches(
                [
                    _create_batch(identity),
                    patch("function_meanings", "one"),
                    patch("table_meanings", "two"),
                ],
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
            )

    def test_relationship_endpoints_must_be_created_in_the_same_plan(self) -> None:
        source = NodeIdentity("CodeNode", "code:function:1")
        target = NodeIdentity("CodeNode", "code:function:2")
        relationship = RelationshipIdentity("CALLS", source, target)
        properties = {
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
        }
        relationship_batch = ProductWriteBatch.build(
            stage_name="call_links",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_create_operations=[
                RelationshipCreateOperation.from_properties(
                    relationship, properties,
                ),
            ],
            expectations=[_expectation(relationship, properties)],
        )

        with self.assertRaisesRegex(ProductWriteContractError, "source is outside"):
            combine_product_write_batches(
                [relationship_batch],
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
            )

    def test_relationship_property_owner_can_be_separate_from_creator(self) -> None:
        source = NodeIdentity("CodeNode", "code:function:1")
        target = NodeIdentity("Table", "db:orders")

        def node_properties(identity: NodeIdentity) -> dict:
            return {
                "stable_id": identity.stable_id,
                "analysis_scope_id": SCOPE,
                "generation_id": GENERATION,
            }

        structure = ProductWriteBatch.build(
            stage_name="code_structure",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            node_create_operations=[
                NodeCreateOperation.from_properties(source, node_properties(source)),
                NodeCreateOperation.from_properties(target, node_properties(target)),
            ],
            expectations=[
                _expectation(source, node_properties(source)),
                _expectation(target, node_properties(target)),
            ],
        )
        relationship = RelationshipIdentity("READS", source, target)
        control = {"analysis_scope_id": SCOPE, "generation_id": GENERATION}
        link = ProductWriteBatch.build(
            stage_name="data_access_links",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_create_operations=[
                RelationshipCreateOperation.from_properties(relationship, control),
            ],
            expectations=[_expectation(relationship, control)],
        )
        meaning = ProductWriteBatch.build(
            stage_name="function_meanings",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_property_set_operations=[
                RelationshipPropertySetOperation.from_value(
                    relationship,
                    "function_table_usage_summary",
                    "주문을 조회한다.",
                ),
            ],
            expectations=[_expectation(
                relationship,
                {"function_table_usage_summary": "주문을 조회한다."},
            )],
        )

        plan = combine_product_write_batches(
            [structure, link, meaning],
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
        )

        owner_map = {
            (identity_key, key): owner
            for identity_key, key, owner in plan.property_owner_by_identity_and_key
        }
        self.assertEqual(
            "data_access_links",
            owner_map[(relationship.canonical_key(), "analysis_scope_id")],
        )
        self.assertEqual(
            "function_meanings",
            owner_map[(relationship.canonical_key(), "function_table_usage_summary")],
        )

    def test_relationship_patch_target_must_be_created_in_plan(self) -> None:
        relationship = RelationshipIdentity(
            "READS",
            NodeIdentity("CodeNode", "code:function:1"),
            NodeIdentity("Table", "db:orders"),
        )
        patch = ProductWriteBatch.build(
            stage_name="function_meanings",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_property_set_operations=[
                RelationshipPropertySetOperation.from_value(
                    relationship, "meaning_status", "sufficient",
                ),
            ],
            expectations=[_expectation(
                relationship, {"meaning_status": "sufficient"},
            )],
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "patch target is outside",
        ):
            combine_product_write_batches(
                [patch], analysis_scope_id=SCOPE, generation_id=GENERATION,
            )

    def test_batch_scope_and_generation_must_match_the_plan(self) -> None:
        identity = NodeIdentity("CodeNode", "code:function:1")
        batch = _create_batch(identity)

        with self.assertRaisesRegex(ProductWriteContractError, "scope/generation mismatch"):
            combine_product_write_batches(
                [batch],
                analysis_scope_id="analysis-scope/v1:" + "b" * 64,
                generation_id=GENERATION,
            )


if __name__ == "__main__":
    unittest.main()
