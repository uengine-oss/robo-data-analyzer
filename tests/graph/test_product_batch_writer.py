from __future__ import annotations

import re
import unittest

from graph.product_batch_writer import (
    verify_product_plan_before_publication,
    write_product_batch,
)
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
    combine_product_write_batches,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:new"


class _Result:
    def __init__(self, *, record=None, rows=None):
        self._record = record
        self._rows = list(rows or [])

    async def single(self, strict=False):
        del strict
        return self._record

    async def data(self):
        return list(self._rows)


class _Transaction:
    def __init__(self):
        self.building = True
        self.ledger_version = 0
        self.stage_hashes: dict[str, str] = {}
        self.operation_keys: set[str] = set()
        self.nodes: dict[tuple[str, str], dict] = {}
        self.relationships: dict[tuple[str, str, str], dict] = {}
        self.create_query_count = 0

    async def run(self, query: str, **parameters):
        if "product-write-ledger-lock" in query:
            if not self.building:
                return _Result(record=None)
            self.ledger_version += 1
            names = sorted(self.stage_hashes)
            return _Result(record={
                "stage_names": names,
                "stage_hashes": [self.stage_hashes[name] for name in names],
                "operation_keys": sorted(self.operation_keys),
                "ledger_version": self.ledger_version,
            })
        if "product-node-create" in query:
            self.create_query_count += 1
            product_type = re.search(r"CREATE \(node:(\w+)\)", query).group(1)
            rows = []
            for item in parameters["items"]:
                key = product_type, item["stable_id"]
                if key in self.nodes:
                    raise AssertionError(f"duplicate CREATE: {key}")
                self.nodes[key] = dict(item["properties"])
                rows.append({
                    "stable_id": item["stable_id"],
                    "properties": dict(self.nodes[key]),
                })
            return _Result(rows=rows)
        if "product-node-patch" in query:
            product_type = re.search(r"MATCH \(node:(\w+)", query).group(1)
            rows = []
            for item in parameters["items"]:
                key = product_type, item["stable_id"]
                properties = self.nodes.get(key)
                if properties is None:
                    continue
                if (
                    properties.get("analysis_scope_id") != parameters["analysis_scope_id"]
                    or properties.get("generation_id") != parameters["generation_id"]
                ):
                    continue
                properties.update(item["properties"])
                rows.append({
                    "stable_id": item["stable_id"],
                    "properties": dict(properties),
                })
            return _Result(rows=rows)
        if "product-relationship-create" in query:
            relationship_type = re.search(
                r"\[relationship:(\w+)\]", query,
            ).group(1)
            rows = []
            for item in parameters["items"]:
                key = (
                    relationship_type,
                    item["source_stable_id"],
                    item["target_stable_id"],
                )
                self.relationships[key] = dict(item["properties"])
                rows.append({
                    "source_stable_id": item["source_stable_id"],
                    "target_stable_id": item["target_stable_id"],
                    "properties": dict(self.relationships[key]),
                })
            return _Result(rows=rows)
        if "product-relationship-patch" in query:
            relationship_type = re.search(
                r"\[relationship:(\w+)", query,
            ).group(1)
            rows = []
            for item in parameters["items"]:
                key = (
                    relationship_type,
                    item["source_stable_id"],
                    item["target_stable_id"],
                )
                properties = self.relationships.get(key)
                if properties is None:
                    continue
                if (
                    properties.get("analysis_scope_id")
                    != parameters["analysis_scope_id"]
                    or properties.get("generation_id")
                    != parameters["generation_id"]
                ):
                    continue
                properties.update(item["properties"])
                rows.append({
                    "source_stable_id": item["source_stable_id"],
                    "target_stable_id": item["target_stable_id"],
                    "properties": dict(properties),
                })
            return _Result(rows=rows)
        if "product-node-readback" in query:
            product_type = re.search(r"MATCH \(node:(\w+)", query).group(1)
            rows = []
            for stable_id in parameters["stable_ids"]:
                properties = self.nodes.get((product_type, stable_id))
                if properties is not None:
                    rows.append({
                        "stable_id": stable_id,
                        "properties": dict(properties),
                    })
            return _Result(rows=rows)
        if "product-relationship-readback" in query:
            relationship_type = re.search(
                r"\[relationship:(\w+)\]", query,
            ).group(1)
            rows = []
            for item in parameters["items"]:
                key = (
                    relationship_type,
                    item["source_stable_id"],
                    item["target_stable_id"],
                )
                properties = self.relationships.get(key)
                if properties is not None:
                    rows.append({
                        "source_stable_id": item["source_stable_id"],
                        "target_stable_id": item["target_stable_id"],
                        "properties": dict(properties),
                    })
            return _Result(rows=rows)
        if "product-write-ledger-commit" in query:
            if parameters["ledger_version"] != self.ledger_version:
                return _Result(record=None)
            self.stage_hashes = dict(zip(
                parameters["stage_names"],
                parameters["stage_hashes"],
                strict=True,
            ))
            self.operation_keys = set(parameters["operation_keys"])
            return _Result(record={"generation_id": GENERATION})
        raise AssertionError(query)


class _Client:
    def __init__(self, transaction: _Transaction):
        self.analysis_scope_id = SCOPE
        self.generation_id = GENERATION
        self.transaction = transaction

    async def execute_write(self, callback):
        return await callback(self.transaction)


class _PlanTransaction(_Transaction):
    def __init__(self):
        super().__init__()
        self.cross_scope_edge_count = 0

    async def run(self, query: str, **parameters):
        if "product-plan-ledger-readback" in query:
            names = sorted(self.stage_hashes)
            return _Result(record={
                "stage_names": names,
                "stage_hashes": [self.stage_hashes[name] for name in names],
                "operation_keys": sorted(self.operation_keys),
                "ledger_version": self.ledger_version,
            })
        if "product-plan-node-readback" in query:
            return _Result(rows=[
                {
                    "labels": [product_type],
                    "stable_id": stable_id,
                    "properties": dict(properties),
                }
                for (product_type, stable_id), properties
                in sorted(self.nodes.items())
            ])
        if "product-plan-cross-scope-edge-readback" in query:
            return _Result(record={
                "cross_scope_edge_count": self.cross_scope_edge_count,
            })
        if "product-plan-relationship-readback" in query:
            rows = []
            for (
                relationship_type, source_stable_id, target_stable_id,
            ), properties in sorted(self.relationships.items()):
                source_type = next(
                    product_type
                    for product_type, stable_id in self.nodes
                    if stable_id == source_stable_id
                )
                target_type = next(
                    product_type
                    for product_type, stable_id in self.nodes
                    if stable_id == target_stable_id
                )
                rows.append({
                    "source_labels": [source_type],
                    "source_stable_id": source_stable_id,
                    "relationship_type": relationship_type,
                    "target_labels": [target_type],
                    "target_stable_id": target_stable_id,
                    "properties": dict(properties),
                })
            return _Result(rows=rows)
        return await super().run(query, **parameters)


def _node_create_batch(*, name: str = "run") -> ProductWriteBatch:
    identity = NodeIdentity("CodeNode", "code:function:1")
    properties = {
        "stable_id": identity.stable_id,
        "analysis_scope_id": SCOPE,
        "generation_id": GENERATION,
        "code_kind": "function",
        "name": name,
    }
    return ProductWriteBatch.build(
        stage_name="code_structure",
        analysis_scope_id=SCOPE,
        generation_id=GENERATION,
        node_create_operations=[
            NodeCreateOperation.from_properties(identity, properties),
        ],
        expectations=[ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=properties,
            present_properties=properties,
        )],
    )


class ProductBatchWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_batch_writes_readbacks_and_commits_ledger(self) -> None:
        transaction = _Transaction()
        batch = _node_create_batch()

        receipt = await write_product_batch(_Client(transaction), batch)

        self.assertFalse(receipt.idempotent_replay)
        self.assertEqual(batch.canonical_batch_sha256, receipt.canonical_batch_sha256)
        self.assertEqual(1, receipt.operation_count)
        self.assertEqual(1, transaction.create_query_count)
        self.assertEqual(
            {batch.stage_name: batch.canonical_batch_sha256},
            transaction.stage_hashes,
        )
        self.assertEqual(1, len(transaction.operation_keys))

    async def test_exact_replay_only_readbacks_without_second_create(self) -> None:
        transaction = _Transaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)

        receipt = await write_product_batch(client, batch)

        self.assertTrue(receipt.idempotent_replay)
        self.assertEqual(1, transaction.create_query_count)

    async def test_same_stage_cannot_replay_with_changed_expected_values(self) -> None:
        transaction = _Transaction()
        client = _Client(transaction)
        await write_product_batch(client, _node_create_batch(name="first"))

        with self.assertRaisesRegex(
            ProductWriteContractError, "changed its immutable batch",
        ):
            await write_product_batch(client, _node_create_batch(name="second"))

    async def test_replay_detects_db_value_changed_after_initial_write(self) -> None:
        transaction = _Transaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)
        transaction.nodes[("CodeNode", "code:function:1")]["name"] = "tampered"

        with self.assertRaisesRegex(
            ProductWriteContractError, "property value mismatch",
        ):
            await write_product_batch(client, batch)

    async def test_patch_and_relationship_are_verified_in_their_own_transactions(self) -> None:
        transaction = _Transaction()
        client = _Client(transaction)
        source = NodeIdentity("CodeNode", "code:function:1")
        target = NodeIdentity("CodeNode", "code:function:2")
        await write_product_batch(client, _node_create_batch())

        target_properties = {
            "stable_id": target.stable_id,
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
            "code_kind": "function",
            "name": "callee",
        }
        target_batch = ProductWriteBatch.build(
            stage_name="components",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            node_create_operations=[
                NodeCreateOperation.from_properties(target, target_properties),
            ],
            expectations=[ProductRecordExpectation.from_properties(
                identity=target,
                closed_property_keys=target_properties,
                present_properties=target_properties,
            )],
        )
        await write_product_batch(client, target_batch)

        patch_properties = {"summary": "호출자를 처리한다."}
        patch_batch = ProductWriteBatch.build(
            stage_name="function_meanings",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            node_property_set_operations=[
                NodePropertySetOperation.from_value(
                    source, "summary", patch_properties["summary"],
                ),
            ],
            expectations=[ProductRecordExpectation.from_properties(
                identity=source,
                closed_property_keys=patch_properties,
                present_properties=patch_properties,
            )],
        )
        await write_product_batch(client, patch_batch)

        relationship = RelationshipIdentity("CALLS", source, target)
        relationship_properties = {
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
        }
        relationship_batch = ProductWriteBatch.build(
            stage_name="call_links",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_create_operations=[
                RelationshipCreateOperation.from_properties(
                    relationship, relationship_properties,
                ),
            ],
            expectations=[ProductRecordExpectation.from_properties(
                identity=relationship,
                closed_property_keys=relationship_properties,
                present_properties=relationship_properties,
            )],
        )
        receipt = await write_product_batch(client, relationship_batch)

        self.assertEqual(1, receipt.operation_count)
        self.assertIn(
            ("CALLS", source.stable_id, target.stable_id),
            transaction.relationships,
        )

        relationship_patch = ProductWriteBatch.build(
            stage_name="module_summary_rollups",
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            relationship_property_set_operations=[
                RelationshipPropertySetOperation.from_value(
                    relationship, "summary_rollup_count", 1,
                ),
            ],
            expectations=[ProductRecordExpectation.from_properties(
                identity=relationship,
                closed_property_keys=("summary_rollup_count",),
                present_properties={"summary_rollup_count": 1},
            )],
        )
        patch_receipt = await write_product_batch(client, relationship_patch)
        self.assertEqual(1, patch_receipt.operation_count)
        self.assertEqual(
            1,
            transaction.relationships[
                ("CALLS", source.stable_id, target.stable_id)
            ]["summary_rollup_count"],
        )

    async def test_missing_building_generation_fails_before_product_write(self) -> None:
        transaction = _Transaction()
        transaction.building = False

        with self.assertRaisesRegex(
            ProductWriteContractError, "building AnalysisGeneration is missing",
        ):
            await write_product_batch(_Client(transaction), _node_create_batch())
        self.assertEqual(0, transaction.create_query_count)

    async def test_client_scope_and_generation_must_match_batch(self) -> None:
        transaction = _Transaction()
        client = _Client(transaction)
        client.analysis_scope_id = "different"

        with self.assertRaisesRegex(ProductWriteContractError, "analysis_scope_id"):
            await write_product_batch(client, _node_create_batch())

    async def test_finalizer_verifies_exact_stage_operation_and_product_closure(self) -> None:
        transaction = _PlanTransaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)
        plan = combine_product_write_batches(
            [batch], analysis_scope_id=SCOPE, generation_id=GENERATION,
        )

        receipt = await client.execute_write(
            lambda tx: verify_product_plan_before_publication(tx, plan)
        )

        self.assertEqual(((batch.stage_name, batch.canonical_batch_sha256),), receipt.stage_hash_by_name)
        self.assertEqual(1, receipt.operation_count)
        self.assertEqual(1, len(receipt.record_hash_by_identity))

    async def test_finalizer_rejects_unexpected_product_property(self) -> None:
        transaction = _PlanTransaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)
        transaction.nodes[("CodeNode", "code:function:1")]["noise"] = "unexpected"
        plan = combine_product_write_batches(
            [batch], analysis_scope_id=SCOPE, generation_id=GENERATION,
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "property closure mismatch",
        ):
            await client.execute_write(
                lambda tx: verify_product_plan_before_publication(tx, plan)
            )

    async def test_finalizer_rejects_missing_operation_ledger_entry(self) -> None:
        transaction = _PlanTransaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)
        transaction.operation_keys.clear()
        plan = combine_product_write_batches(
            [batch], analysis_scope_id=SCOPE, generation_id=GENERATION,
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "product operation ledger mismatch",
        ):
            await client.execute_write(
                lambda tx: verify_product_plan_before_publication(tx, plan)
            )

    async def test_finalizer_rejects_unknown_generation_owned_node_label(self) -> None:
        transaction = _PlanTransaction()
        client = _Client(transaction)
        batch = _node_create_batch()
        await write_product_batch(client, batch)
        transaction.nodes[("LegacyNoise", "legacy:1")] = {
            "stable_id": "legacy:1",
            "analysis_scope_id": SCOPE,
            "generation_id": GENERATION,
        }
        plan = combine_product_write_batches(
            [batch], analysis_scope_id=SCOPE, generation_id=GENERATION,
        )

        with self.assertRaisesRegex(
            ProductWriteContractError, "closed product label",
        ):
            await client.execute_write(
                lambda tx: verify_product_plan_before_publication(tx, plan)
            )


if __name__ == "__main__":
    unittest.main()
