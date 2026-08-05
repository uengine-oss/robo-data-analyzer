"""응답 캐시 계약 — 키는 보낸 것 전부, 실패는 미적중으로 강등 (spec 091)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import BaseModel, create_model

from llm import response_cache


class _Out(BaseModel):
    text: str = ""


def _settings(root: Path, enabled: bool = True, max_mb: int = 512):
    return mock.patch.object(
        response_cache,
        "SETTINGS",
        SimpleNamespace(
            analysis_llm=SimpleNamespace(response_cache=enabled, response_cache_max_mb=max_mb),
            analysis_data=SimpleNamespace(dir=root),
        ),
    )


_BASE = dict(rule_name="code_summary", system_prompt="sys", user_content="본문",
             model="m", max_tokens=100, structured_output_method="json_schema",
             response_model=_Out)


class KeyTest(unittest.TestCase):
    def test_every_sent_field_changes_the_key(self):
        base = response_cache.make_key(**_BASE)
        for field, other in (("system_prompt", "다름"), ("user_content", "다름"),
                             ("model", "m2"), ("max_tokens", 200),
                             ("structured_output_method", "function_calling"),
                             ("rule_name", "other")):
            self.assertNotEqual(base, response_cache.make_key(**{**_BASE, field: other}),
                                f"{field} 가 바뀌었는데 키가 같다 — 낡은 답이 살아남는다")

    def test_same_input_same_key(self):
        self.assertEqual(response_cache.make_key(**_BASE), response_cache.make_key(**_BASE))

    def test_same_class_name_with_different_json_schema_changes_key(self):
        first = create_model("DynamicOutput", first=(int, ...))
        second = create_model("DynamicOutput", second=(int, ...))
        self.assertNotEqual(
            response_cache.make_key(**{**_BASE, "response_model": first}),
            response_cache.make_key(**{**_BASE, "response_model": second}),
        )


class RoundTripTest(unittest.TestCase):
    def test_store_then_load_returns_equal_model(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = response_cache.make_key(**_BASE)
            response_cache.store(key, _Out(text="요약"))
            self.assertEqual(response_cache.load(key, _Out), _Out(text="요약"))

    def test_plaintext_value_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            response_cache.store("k" * 64, "평문 결과")
            self.assertEqual(response_cache.load("k" * 64, None), "평문 결과")

    def test_disabled_never_stores_or_loads(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp), enabled=False):
            response_cache.store("k" * 64, _Out(text="x"))
            self.assertIsNone(response_cache.load("k" * 64, _Out))
            self.assertEqual(list(Path(tmp).rglob("*.json")), [])


class DegradationTest(unittest.TestCase):
    def test_corrupt_entry_is_a_miss_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = "a" * 64
            path = Path(tmp) / ".llm-cache" / key[:2] / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{깨진 json", encoding="utf-8")
            self.assertIsNone(response_cache.load(key, _Out))

    def test_schema_mismatch_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = "b" * 64
            path = Path(tmp) / ".llm-cache" / key[:2] / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"value": {"unknown": 1}}), encoding="utf-8")

            class _Strict(BaseModel):
                required: int

            self.assertIsNone(response_cache.load(key, _Strict))

    def test_cache_touch_failure_is_visible_without_losing_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = "e" * 64
            response_cache.store(key, _Out(text="kept"))
            with (
                mock.patch.object(response_cache.os, "utime", side_effect=OSError("touch")),
                mock.patch.object(response_cache, "log_warning") as warning,
            ):
                self.assertEqual(_Out(text="kept"), response_cache.load(key, _Out))
            warning.assert_called_once()
            self.assertEqual("cache_touch_failed", warning.call_args.args[1])




class AtomicWriteTest(unittest.TestCase):
    """동시 쓰기에도 반쪽 파일이 남지 않는다 — 임시파일 → rename."""

    def test_no_partial_file_remains_after_store(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = "c" * 64
            response_cache.store(key, _Out(text="값"))
            root = Path(tmp) / ".llm-cache"
            self.assertEqual([p.name for p in root.rglob("*.tmp")], [],
                             "임시 파일이 남았다 — rename 이 안 됐다")
            self.assertEqual(response_cache.load(key, _Out), _Out(text="값"))

    def test_overwrite_keeps_readable_entry(self):
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            key = "d" * 64
            response_cache.store(key, _Out(text="첫값"))
            response_cache.store(key, _Out(text="둘째값"))
            self.assertEqual(response_cache.load(key, _Out), _Out(text="둘째값"))

class SweepTest(unittest.TestCase):
    """상한 초과 시 오래 안 쓴 것부터 지운다 — 안 지우면 코퍼스 옆에서 무한히 쌓인다."""

    def _fill(self, root: Path, count: int, payload_chars: int) -> list[Path]:
        paths = []
        for index in range(count):
            key = f"{index:064d}"
            path = root / ".llm-cache" / key[:2] / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"value": "x" * payload_chars}), encoding="utf-8")
            os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))  # 오래된 것부터
            paths.append(path)
        return paths

    def test_over_limit_evicts_least_recently_used_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp), max_mb=1):
            paths = self._fill(Path(tmp), count=20, payload_chars=100 * 1024)  # 약 2MB

            response_cache.sweep()

            survivors = [p for p in paths if p.exists()]
            self.assertTrue(survivors, "전부 지웠다 — 목표치 계산이 틀렸다")
            self.assertLess(len(survivors), len(paths), "상한을 넘겼는데 아무것도 안 지웠다")
            self.assertEqual(paths[len(paths) - len(survivors):], survivors,
                             "최근에 쓴 것이 지워졌다 — LRU 순서가 아니다")
            total = sum(p.stat().st_size for p in survivors)
            self.assertLessEqual(total, 1024 * 1024, "정리 후에도 상한을 넘는다")

    def test_under_limit_deletes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp), max_mb=512):
            paths = self._fill(Path(tmp), count=5, payload_chars=1024)

            response_cache.sweep()

            self.assertTrue(all(p.exists() for p in paths))

    def test_partial_sweep_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp), max_mb=1):
            self._fill(Path(tmp), count=20, payload_chars=100 * 1024)
            with (
                mock.patch.object(Path, "unlink", side_effect=OSError("locked")),
                mock.patch.object(response_cache, "log_warning") as warning,
            ):
                response_cache.sweep()

            self.assertTrue(any(
                call.args[1] == "cache_sweep_partial_failure"
                for call in warning.call_args_list
            ))

    def test_disabled_limit_never_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp), max_mb=0):
            paths = self._fill(Path(tmp), count=5, payload_chars=100 * 1024)

            response_cache.sweep()

            self.assertTrue(all(p.exists() for p in paths))

    def test_load_refreshes_recency_so_used_entries_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _settings(Path(tmp)):
            paths = self._fill(Path(tmp), count=3, payload_chars=64)
            oldest = paths[0]
            before = oldest.stat().st_mtime

            response_cache.load(oldest.stem, None)

            self.assertGreater(oldest.stat().st_mtime, before,
                               "적중했는데 최근성이 그대로다 — LRU 가 쓰는 것을 지운다")


if __name__ == "__main__":
    unittest.main()
