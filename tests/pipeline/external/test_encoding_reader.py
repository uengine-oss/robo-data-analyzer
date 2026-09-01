from __future__ import annotations

import codecs
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.stages.external.encoding_reader import TextDecodeError, decode_bytes, read_text
from shared.files.source_cache import SourceFileCache, SourceFileReadError


class EncodingReaderTest(unittest.TestCase):
    def test_bom_utf8_cp949_and_euc_kr_preserve_korean(self) -> None:
        value = "주문조회 한글설명"
        cases = (
            (codecs.BOM_UTF8 + value.encode("utf-8"), "utf-8-sig"),
            (codecs.BOM_UTF16_LE + value.encode("utf-16-le"), "utf-16"),
            (value.encode("cp949"), "cp949"),
            # EUC-KR byte sequences are a strict subset of CP949, so either
            # truthful decoder name is acceptable when the text is identical.
            (value.encode("euc-kr"), "korean"),
        )
        for raw, expected_encoding in cases:
            with self.subTest(expected_encoding=expected_encoding):
                decoded = decode_bytes(raw)
                self.assertEqual(value, decoded.text)
                if expected_encoding == "korean":
                    self.assertIn(decoded.encoding.lower().replace("-", "_"), {"cp949", "euc_kr"})
                else:
                    self.assertIn(expected_encoding, decoded.encoding.lower())

    def test_false_xml_declaration_does_not_override_actual_bytes(self) -> None:
        text = '<?xml version="1.0" encoding="UTF-8"?><x>상품</x>'
        self.assertEqual(text, decode_bytes(text.encode("cp949")).text)

    def test_binary_and_low_confidence_bytes_fail_explicitly(self) -> None:
        for raw in (b"abc\x00def", b"\xff"):
            with self.subTest(raw=raw), self.assertRaises(TextDecodeError):
                decode_bytes(raw)

    def test_binary_marker_after_old_probe_window_is_still_rejected(self) -> None:
        raw = (b"plain text\n" * 900) + b"\x00binary tail"

        with self.assertRaises(TextDecodeError):
            decode_bytes(raw)

    def test_shared_cache_reads_once_and_replays_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.txt"
            path.write_text("내용", encoding="utf-8")
            cache = SourceFileCache()
            original_read_bytes = Path.read_bytes
            with patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=lambda instance: original_read_bytes(instance),
            ) as read_bytes:
                self.assertEqual("내용", read_text(path, cache).text)
                self.assertEqual("내용", read_text(path, cache).text)
                self.assertEqual(1, read_bytes.call_count)
            missing = Path(temp) / "missing.txt"
            with self.assertRaises(SourceFileReadError):
                cache.read_bytes(missing)
            with self.assertRaises(SourceFileReadError):
                cache.read_bytes(missing)


if __name__ == "__main__":
    unittest.main()
