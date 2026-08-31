"""Parser call facts are the only external-reference source."""
from __future__ import annotations

import unittest

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import Constant
from graph.schema.node_ids import external_chunk_id, project_file_id
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.steps.step3_load_external.span_verifier import sha256_text
from pipeline.steps.step5_linking.link_external.linker import (
    ExternalLinkContractError,
    link_external,
)


def _chunk(key: str, *, ordinal: int = 0, keys: tuple[str, ...] | None = None):
    relpath = f"external/record-{ordinal}.yaml"
    file_id = project_file_id("project", relpath)
    text = f"{key}: value\n"
    record_id = external_chunk_id(file_id, 0, len(text))
    return ExternalSourceRecord(
        source_record_id=record_id,
        source_file_id=file_id,
        external_adapter_id="analyzer.external.yaml",
        external_adapter_version="analyzer-external-span-adapter/v1",
        addressable_keys=keys or (key,),
        semantic_kind="yaml_entry",
        exact_text=text,
        project_relative_path=relpath,
        source_character_start=0,
        source_character_end=len(text),
        source_start_line=1,
        source_end_line=1,
        provenance="source_span",
        source_sha256=sha256_text(text),
        content_sha256=sha256_text(text),
    )


def _call(
    call_id: str,
    resolver: str,
    arguments: tuple[str, ...],
    *,
    owner: str = "code:Service.run",
    presence: str = "active",
):
    return {
        "call_id": call_id,
        "caller_id": owner,
        "call_text": resolver,
        "arguments": [
            {
                "source": value,
                "range": {"charOffset": index, "charLength": len(value)},
                "syntax_kind": "expression",
                "literal_kind": None,
                "literal_value": None,
                "identifier": None,
            }
            for index, value in enumerate(arguments)
        ],
        "file": "Service.java",
        "line": 17,
        "presence_status": presence,
    }


class ExternalReferenceLinkerTest(unittest.TestCase):
    def test_exact_literal_argument_links_once(self) -> None:
        chunk = _chunk("ORDER.API")
        outcome = link_external(
            call_sites=[_call("call:1", "selectOne", ('"ORDER.API"', "request"))],
            constants=[],
            source_records=[chunk],
        )

        self.assertEqual(1, len(outcome.resolved_external_reference_links))
        self.assertEqual(
            chunk.source_record_id,
            outcome.resolved_external_reference_links[0].external_source_record_id,
        )
        self.assertEqual(("call:1:external-argument:0",), (
            *outcome.external_reference_ledger.required_external_reference_occurrence_ids,
        ))
        resolution = outcome.external_reference_ledger.occurrences[0].resolution
        self.assertEqual(
            chunk.source_record_id, resolution.linked.external_source_record_id,
        )

    def test_non_resolver_call_never_becomes_an_external_reference(self) -> None:
        outcome = link_external(
            call_sites=[_call("call:1", "put", ('"ORDER.API"',))],
            constants=[],
            source_records=[_chunk("ORDER.API")],
        )
        self.assertEqual([], outcome.resolved_external_reference_links)
        self.assertEqual((), outcome.external_reference_ledger.occurrences)

    def test_referenced_literal_constant_is_resolved_without_source_search(self) -> None:
        chunk = _chunk("ORDER.API")
        constant = Constant(
            id="constant:ORDER_KEY",
            owner_id="code:Service",
            name="ORDER_KEY",
            file_path="Service.java",
            start_line=3,
            end_line=3,
            label="CONSTANT_FIELD",
            value='"ORDER.API"',
        )
        outcome = link_external(
            call_sites=[_call("call:1", "selectOne", ("Keys.ORDER_KEY",))],
            constants=[constant],
            source_records=[chunk],
            references_edges=[References("code:Service.run", constant.id, 17)],
        )
        self.assertEqual(
            chunk.source_record_id,
            outcome.resolved_external_reference_links[0].external_source_record_id,
        )

    def test_dynamic_and_missing_arguments_are_accounted_not_linked(self) -> None:
        outcome = link_external(
            call_sites=[
                _call("call:1", "selectOne", ("buildKey()",)),
                _call("call:2", "selectOne", ()),
            ],
            constants=[],
            source_records=[_chunk("ORDER.API")],
        )
        self.assertEqual([], outcome.resolved_external_reference_links)
        reasons = [
            item.resolution.unresolved.reason_code
            for item in outcome.external_reference_ledger.occurrences
        ]
        self.assertEqual(["dynamic_argument", "missing_argument"], reasons)

    def test_duplicate_key_targets_are_ambiguous_not_all_linked(self) -> None:
        left = _chunk("SHARED", ordinal=1)
        right = _chunk("SHARED", ordinal=2)
        outcome = link_external(
            call_sites=[_call("call:1", "lookup", ('"SHARED"',))],
            constants=[],
            source_records=[left, right],
        )
        self.assertEqual([], outcome.resolved_external_reference_links)
        unresolved = outcome.external_reference_ledger.occurrences[0].resolution.unresolved
        self.assertEqual("ambiguous_external_key", unresolved.reason_code)
        self.assertEqual(
            tuple(sorted((left.source_record_id, right.source_record_id))),
            unresolved.candidate_target_ids,
        )

    def test_inactive_call_is_rejected_without_losing_required_occurrence(self) -> None:
        outcome = link_external(
            call_sites=[_call(
                "call:1", "lookup", ('"ORDER.API"',), presence="inactive",
            )],
            constants=[],
            source_records=[_chunk("ORDER.API")],
        )
        self.assertEqual([], outcome.resolved_external_reference_links)
        rejected = (
            outcome.external_reference_ledger.occurrences[0]
            .resolution.rejected_not_external_reference
        )
        self.assertEqual("inactive_call", rejected.reason_code)

    def test_direct_file_hit_stays_unresolved_until_it_has_a_verified_record(self) -> None:
        external_file = ExternalSourceFile(
            source_file_id="file:project/data/orders.dat",
            project_relative_path="data/orders.dat",
            extension=".dat",
            category="unknown",
            status="unanalyzed",
            disposition_reason="no_span_adapter",
        )
        outcome = link_external(
            call_sites=[_call("call:1", "fopen", ('"orders.dat"',))],
            constants=[],
            source_records=[],
            source_files=[external_file],
        )
        unresolved = outcome.external_reference_ledger.occurrences[0].resolution.unresolved
        self.assertEqual("file_target_has_no_verified_record", unresolved.reason_code)

    def test_long_literal_and_multiple_alias_occurrences_are_lossless(self) -> None:
        long_key = "K" * 241
        chunk = _chunk(
            long_key,
            keys=(long_key, "ORDER.ALIAS"),
        )
        outcome = link_external(
            call_sites=[
                _call("call:1", "fopen", (f'"{long_key}"',)),
                _call("call:2", "selectOne", ('"ORDER.ALIAS"',)),
            ],
            constants=[],
            source_records=[chunk],
        )
        self.assertEqual(2, len(outcome.resolved_external_reference_links))
        self.assertEqual(2, len(outcome.external_reference_ledger.occurrences))

    def test_invalid_parser_call_identity_or_coordinate_fails_closed(self) -> None:
        duplicate = _call("call:1", "lookup", ('"A"',))
        with self.assertRaises(ExternalLinkContractError):
            link_external(
                call_sites=[duplicate, duplicate], constants=[], source_records=[],
            )
        invalid_line = {**duplicate, "call_id": "call:2", "line": 0}
        with self.assertRaises(ExternalLinkContractError):
            link_external(
                call_sites=[invalid_line], constants=[], source_records=[],
            )


if __name__ == "__main__":
    unittest.main()
