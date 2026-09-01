"""Span adapters vs. gold corpus (spec 078 S2/T204).

Boundary gold precision must be 100%: for every fixture the adapter output
(text, keys, semantic_kind) must equal the answer key exactly, and verified
chunks must satisfy the slice/hash invariants.  fail_closed fixtures must
raise ``AdapterParseError`` — never partially succeed.
"""
from __future__ import annotations

import unittest

from pipeline.stages.external.parsers.span_common import AdapterParseError
from pipeline.stages.external.parsers.span_config import (
    parse_csv_spans,
    parse_ini_spans,
    parse_json_spans,
    parse_jsonl_spans,
    parse_properties_spans,
    parse_yaml_spans,
)
from pipeline.stages.external.parsers.span_markdown import parse_markdown_spans
from pipeline.stages.external.parsers.span_xml import parse_xml_spans
from pipeline.stages.external.span_verifier import (
    SpanVerificationError,
    verify_spans,
)
from tests.pipeline.external.gold.gold_loader import iter_gold


ADAPTERS = {
    "xml": parse_xml_spans,
    "yaml": parse_yaml_spans,
    "json": parse_json_spans,
    "jsonl": parse_jsonl_spans,
    "md": parse_markdown_spans,
    "properties": parse_properties_spans,
    "ini": parse_ini_spans,
    "csv": parse_csv_spans,
}


class SpanAdapterGoldTest(unittest.TestCase):
    def test_every_gold_case_matches_exactly(self) -> None:
        for case in iter_gold():
            adapter = ADAPTERS[case.format]
            with self.subTest(case=case.stem):
                if case.fail_closed:
                    with self.assertRaises(AdapterParseError):
                        adapter(case.source)
                    continue
                chunks = verify_spans(case.source, adapter(case.source))
                actual = [(chunk.text, list(chunk.keys), chunk.semantic_kind) for chunk in chunks]
                expected = [
                    (chunk["text"], chunk["keys"], chunk["semantic_kind"])
                    for chunk in case.chunks
                ]
                self.assertEqual(expected, actual)
                for chunk in chunks:
                    self.assertEqual(chunk.text, case.source[chunk.char_start:chunk.char_end])

    def test_verifier_rejects_overlap_and_bounds(self) -> None:
        from pipeline.stages.external.parsers.span_common import SemanticSpan

        source = "abcdef"
        overlap = [
            SemanticSpan(0, 4, (), "record", "a"),
            SemanticSpan(2, 6, (), "record", "b"),
        ]
        with self.assertRaises(SpanVerificationError):
            verify_spans(source, overlap)
        with self.assertRaises(SpanVerificationError):
            verify_spans(source, [SemanticSpan(0, 99, (), "record", "x")])
        with self.assertRaises(SpanVerificationError):
            verify_spans("   \n  ", [SemanticSpan(0, 3, (), "record", "x")])


if __name__ == "__main__":
    unittest.main()
