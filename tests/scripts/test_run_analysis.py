"""운영자 분석 CLI가 파이프라인 실패를 성공 exit로 숨기지 않는지 검증."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import run_analysis as subject


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
                        database_override="analyzer_unit",
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
                    database_override="analyzer_unit",
                )

    async def test_failed_project_redacts_operator_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            source = data_dir / "source"
            source.mkdir()
            (source / "sample.c").write_text("int main(void){}", encoding="utf-8")
            output = io.StringIO()

            with (
                patch.object(
                    subject,
                    "analyze_one",
                    new=AsyncMock(side_effect=RuntimeError("secret-value")),
                ),
                patch.object(subject, "set_override"),
                patch.object(
                    subject,
                    "redact_sensitive_text",
                    return_value="RuntimeError: [REDACTED]",
                ) as redact,
                redirect_stdout(output),
            ):
                with self.assertRaises(subject.AnalysisBatchError):
                    await subject.main(
                        None,
                        "framework",
                        data_dir_override=data_dir,
                        database_override="analyzer_unit",
                    )

        redact.assert_called_once_with("RuntimeError: secret-value")
        self.assertIn("[FAIL] source: RuntimeError: [REDACTED]", output.getvalue())
        self.assertNotIn("secret-value", output.getvalue())

    async def test_missing_target_is_an_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "source").mkdir()
            with self.assertRaisesRegex(ValueError, "디렉토리 없음"):
                await subject.main(
                    "missing",
                    "framework",
                    data_dir_override=data_dir,
                    database_override="analyzer_unit",
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
                    database_override="analyzer_unit",
                )


if __name__ == "__main__":
    unittest.main()
