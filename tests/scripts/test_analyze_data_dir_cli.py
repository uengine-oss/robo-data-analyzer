"""운영자 분석 CLI가 파이프라인 실패를 성공 exit로 숨기지 않는지 검증."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import analyze_data_dir as subject


class AnalyzeDataDirCliTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_project_is_raised_after_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            source = data_dir / "source"
            source.mkdir()
            (source / "sample.c").write_text("int main(void){}", encoding="utf-8")

            with (
                patch.object(subject, "analyze_one", new=AsyncMock(side_effect=RuntimeError("boom"))),
                patch.object(subject, "set_override"),
            ):
                with self.assertRaises(subject.AnalysisBatchError) as raised:
                    await subject.main(
                        None,
                        "framework",
                        data_dir_override=data_dir,
                        database_override="test",
                    )

        self.assertEqual([source], [path for path, _ in raised.exception.failures])

    async def test_successful_project_returns_normally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            source = data_dir / "source"
            source.mkdir()
            (source / "sample.c").write_text("int main(void){}", encoding="utf-8")

            with (
                patch.object(subject, "analyze_one", new=AsyncMock(return_value=SimpleNamespace())),
                patch.object(subject, "set_override"),
            ):
                await subject.main(
                    None,
                    "framework",
                    data_dir_override=data_dir,
                    database_override="test",
                )

    async def test_missing_target_is_an_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "source").mkdir()
            with self.assertRaisesRegex(ValueError, "디렉토리 없음"):
                await subject.main(
                    "missing",
                    "framework",
                    data_dir_override=data_dir,
                    database_override="test",
                )

    async def test_empty_source_is_an_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "source").mkdir()
            with self.assertRaisesRegex(ValueError, "비어 있음"):
                await subject.main(
                    None,
                    "framework",
                    data_dir_override=data_dir,
                    database_override="test",
                )


if __name__ == "__main__":
    unittest.main()
