from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.models.human_glossary import load_human_glossary


class HumanGlossaryLoaderTest(unittest.TestCase):
    def _write(self, root: Path, text: str | bytes) -> Path:
        path = root / "analysis" / "glossary.yaml"
        path.parent.mkdir(parents=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def test_absent_file_is_explicit_empty_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            glossary = load_human_glossary(Path(temp))
        self.assertFalse(glossary.supplied)
        self.assertEqual((), glossary.terms)
        self.assertIsNone(glossary.sha256)

    def test_valid_unicode_input_preserves_human_values_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root, """schema_version: \"1.0\"
terms:
  - canonical: 승인 대기
    aliases: [결제 대기, Awaiting Approval]
    meaning: 결제 승인을 기다리는 업무 상태
    category: 결제 상태
""")
            glossary = load_human_glossary(root)
            metadata = glossary.manifest_dict(root.resolve())

        self.assertTrue(glossary.supplied)
        self.assertEqual("승인 대기", glossary.terms[0].canonical)
        self.assertEqual(("결제 대기", "Awaiting Approval"), glossary.terms[0].aliases)
        self.assertEqual("analysis/glossary.yaml", metadata["source"])
        self.assertEqual(64, len(metadata["sha256"]))
        self.assertEqual(1, metadata["term_count"])

    def test_unknown_keys_wrong_types_and_surrounding_whitespace_fail(self) -> None:
        invalid_documents = (
            "schema_version: '1.0'\nterms: []\nextra: true\n",
            "schema_version: '1.0'\nterms: [{canonical: X, aliases: nope, meaning: M, category: ''}]\n",
            "schema_version: '1.0'\nterms: [{canonical: ' X', aliases: [], meaning: M, category: ''}]\n",
        )
        for document in invalid_documents:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self._write(root, document)
                with self.subTest(document=document), self.assertRaises(ValueError):
                    load_human_glossary(root)

    def test_casefold_collisions_fail_across_canonical_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root, """schema_version: "1.0"
terms:
  - canonical: Payment Hold
    aliases: [결제 보류]
    meaning: 첫 뜻
    category: ""
  - canonical: 결제 보류
    aliases: []
    meaning: 둘째 뜻
    category: ""
""")
            with self.assertRaisesRegex(ValueError, "invalid human glossary contract") as caught:
                load_human_glossary(root)
            self.assertNotIn("Payment Hold", str(caught.exception))
            self.assertNotIn("결제 보류", str(caught.exception))

    def test_invalid_utf8_fails_instead_of_replacing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root, b"schema_version: \xff")
            with self.assertRaisesRegex(ValueError, "strict UTF-8"):
                load_human_glossary(root)

    def test_valid_content_is_not_rejected_by_arbitrary_size_or_count_caps(self) -> None:
        aliases = [f"별칭-{index}" for index in range(25)]
        canonical = "표준어-" + ("가" * 80)
        meaning = "뜻" * 300
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root, "\n".join((
                'schema_version: "1.0"',
                "terms:",
                f"  - canonical: {canonical}",
                "    aliases:",
                *(f"      - {alias}" for alias in aliases),
                f"    meaning: {meaning}",
                "    category: 업무 상태",
            )))
            glossary = load_human_glossary(root)

        self.assertEqual(canonical, glossary.terms[0].canonical)
        self.assertEqual(tuple(aliases), glossary.terms[0].aliases)
        self.assertEqual(meaning, glossary.terms[0].meaning)


if __name__ == "__main__":
    unittest.main()
