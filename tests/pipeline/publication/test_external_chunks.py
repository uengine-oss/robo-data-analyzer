from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.models.external_reference_resolution import (
    ExternalReferenceOccurrence,
    ExternalReferenceResolutionLedger,
    ResolvedExternalReferenceLink,
)
from pipeline.models.external_source import ExternalSourceRecord
from pipeline.publication.external_chunks import (
    ExternalChunksProjectionError,
    build_external_chunks_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"
TEXT = "service:\n  endpoint: /orders\n"


def _routine(stable_id: str = "code:orders.c:load_orders") -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature="load_orders(void)",
        file_path="orders.c",
        start_line=1,
        end_line=20,
        label="FUNCTION",
        name="load_orders",
        owner_id="code:orders.c",
    )


def _source_record(
    *,
    relpath: str = "config/services.yaml",
    keys: tuple[str, ...] = ("service", "service.endpoint"),
    addressable_keys: tuple[str, ...] = ("service", "service.endpoint"),
    content_hash: str = "b" * 64,
) -> ExternalSourceRecord:
    file_id = project_file_id("skt", relpath)
    record_id = external_chunk_id(file_id, 0, len(TEXT))
    return ExternalSourceRecord(
        source_record_id=record_id,
        source_file_id=file_id,
        external_adapter_id="analyzer.external.yaml",
        external_adapter_version="analyzer-external-span-adapter/v1",
        addressable_keys=addressable_keys,
        semantic_kind="yaml_entry",
        exact_text=TEXT,
        project_relative_path=relpath,
        source_character_start=0,
        source_character_end=len(TEXT),
        source_start_line=1,
        source_end_line=2,
        provenance="source_span",
        source_sha256="c" * 64,
        content_sha256=content_hash,
    )


def _occurrence(
    occurrence_id: str,
    *,
    owner_id: str,
    record_id: str | None = None,
    external_key: str = "service.endpoint",
) -> ExternalReferenceOccurrence:
    resolution = (
        {
            "linked": {
                "external_source_record_id": record_id,
                "external_key": external_key,
            }
        }
        if record_id is not None
        else {"unresolved": {"reason_code": "dynamic_argument"}}
    )
    return ExternalReferenceOccurrence.model_validate({
        "external_reference_occurrence_id": occurrence_id,
        "parser_call_occurrence_id": f"parser:{occurrence_id}",
        "owner_code_node_id": owner_id,
        "source_file": "orders.c",
        "source_line": 7,
        "resolver_name": "load_external",
        "argument_index": 0,
        "resolution": resolution,
    })


def _ledger(*occurrences: ExternalReferenceOccurrence) -> ExternalReferenceResolutionLedger:
    return ExternalReferenceResolutionLedger(
        required_external_reference_occurrence_ids=tuple(
            item.external_reference_occurrence_id for item in occurrences
        ),
        occurrences=occurrences,
    )


class ExternalChunksPublicationTest(unittest.TestCase):
    def test_linked_exact_key_publishes_closed_node_and_relation(self) -> None:
        routine = _routine()
        record = _source_record()
        ledger = _ledger(_occurrence(
            "external-ref:1",
            owner_id=routine.id,
            record_id=record.source_record_id,
        ))

        batch = build_external_chunks_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            source_records=[record],
            resolved_links=[ResolvedExternalReferenceLink(
                owner_code_node_id=routine.id,
                external_source_record_id=record.source_record_id,
                external_key="service.endpoint",
            )],
            external_reference_ledger=ledger,
        )

        self.assertEqual(1, len(batch.node_create_operations))
        self.assertEqual(1, len(batch.relationship_create_operations))
        node_properties = dict(batch.node_create_operations[0].present_properties)
        self.assertEqual("service.endpoint", node_properties["external_key"])
        self.assertEqual(
            record.source_record_id, node_properties["source_record_identity"],
        )
        self.assertEqual(record.external_adapter_id, node_properties["external_adapter_id"])
        self.assertEqual(record.external_adapter_version, node_properties["external_adapter_version"])
        self.assertEqual(record.exact_text, node_properties["exact_text"])
        relation_properties = dict(
            batch.relationship_create_operations[0].present_properties
        )
        self.assertEqual(
            {"analysis_scope_id": SCOPE, "generation_id": GENERATION},
            relation_properties,
        )

    def test_repeated_occurrences_publish_one_graph_relation(self) -> None:
        routine = _routine()
        record = _source_record()
        occurrences = (
            _occurrence(
                "external-ref:1",
                owner_id=routine.id,
                record_id=record.source_record_id,
            ),
            _occurrence(
                "external-ref:2",
                owner_id=routine.id,
                record_id=record.source_record_id,
            ),
        )
        batch = build_external_chunks_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            source_records=[record],
            resolved_links=[ResolvedExternalReferenceLink(
                routine.id, record.source_record_id, "service.endpoint",
            )],
            external_reference_ledger=_ledger(*occurrences),
        )
        self.assertEqual(1, len(batch.node_create_operations))
        self.assertEqual(1, len(batch.relationship_create_operations))

    def test_two_addressable_aliases_publish_two_exact_key_products(self) -> None:
        routine = _routine()
        record = _source_record()
        occurrences = (
            _occurrence(
                "external-ref:1",
                owner_id=routine.id,
                record_id=record.source_record_id,
                external_key="service",
            ),
            _occurrence(
                "external-ref:2",
                owner_id=routine.id,
                record_id=record.source_record_id,
                external_key="service.endpoint",
            ),
        )
        batch = build_external_chunks_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            source_records=[record],
            resolved_links=[
                ResolvedExternalReferenceLink(
                    routine.id, record.source_record_id, "service",
                ),
                ResolvedExternalReferenceLink(
                    routine.id, record.source_record_id, "service.endpoint",
                ),
            ],
            external_reference_ledger=_ledger(*occurrences),
        )
        self.assertEqual(2, len(batch.node_create_operations))
        self.assertEqual(2, len(batch.relationship_create_operations))
        self.assertEqual(
            {"service", "service.endpoint"},
            {
                dict(operation.present_properties)["external_key"]
                for operation in batch.node_create_operations
            },
        )

    def test_unresolved_occurrence_creates_no_product(self) -> None:
        routine = _routine()
        record = _source_record()
        batch = build_external_chunks_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            source_records=[record],
            resolved_links=[],
            external_reference_ledger=_ledger(_occurrence(
                "external-ref:1", owner_id=routine.id,
            )),
        )
        self.assertEqual((), batch.node_create_operations)
        self.assertEqual((), batch.relationship_create_operations)
        self.assertEqual((), batch.expectations)

    def test_edge_and_ledger_difference_fails_closed(self) -> None:
        routine = _routine()
        record = _source_record()
        with self.assertRaisesRegex(
            ExternalChunksProjectionError, "differ from linked",
        ):
            build_external_chunks_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[routine],
                source_records=[record],
                resolved_links=[],
                external_reference_ledger=_ledger(_occurrence(
                    "external-ref:1",
                    owner_id=routine.id,
                    record_id=record.source_record_id,
                )),
            )

    def test_missing_record_nonroutine_owner_and_nonaddressable_key_fail(self) -> None:
        routine = _routine()
        record = _source_record()
        cases = (
            (
                [],
                _ledger(_occurrence(
                    "external-ref:1",
                    owner_id=routine.id,
                    record_id=record.source_record_id,
                )),
                "record is missing",
            ),
            (
                [record],
                _ledger(_occurrence(
                    "external-ref:1",
                    owner_id="code:not-a-routine",
                    record_id=record.source_record_id,
                )),
                "owner is not a routine",
            ),
            (
                [record],
                _ledger(_occurrence(
                    "external-ref:1",
                    owner_id=routine.id,
                    record_id=record.source_record_id,
                    external_key="not.addressable",
                )),
                "not addressable",
            ),
        )
        for records, ledger, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ExternalChunksProjectionError, message,
            ):
                build_external_chunks_write_batch(
                    analysis_scope_id=SCOPE,
                    generation_id=GENERATION,
                    code_nodes=[routine],
                    source_records=records,
                    resolved_links=[],
                    external_reference_ledger=ledger,
                )

    def test_stable_identity_depends_on_file_key_and_exact_content(self) -> None:
        routine = _routine()

        def published_id(record: ExternalSourceRecord, key: str) -> str:
            batch = build_external_chunks_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[routine],
                source_records=[record],
                resolved_links=[ResolvedExternalReferenceLink(
                    routine.id, record.source_record_id, key,
                )],
                external_reference_ledger=_ledger(_occurrence(
                    "external-ref:1",
                    owner_id=routine.id,
                    record_id=record.source_record_id,
                    external_key=key,
                )),
            )
            return batch.node_create_operations[0].identity.stable_id

        original = _source_record()
        self.assertEqual(
            published_id(original, "service.endpoint"),
            published_id(original, "service.endpoint"),
        )
        self.assertNotEqual(
            published_id(original, "service"),
            published_id(original, "service.endpoint"),
        )
        self.assertNotEqual(
            published_id(original, "service.endpoint"),
            published_id(_source_record(relpath="config/other.yaml"), "service.endpoint"),
        )
        self.assertNotEqual(
            published_id(original, "service.endpoint"),
            published_id(_source_record(content_hash="d" * 64), "service.endpoint"),
        )


if __name__ == "__main__":
    unittest.main()
