"""Runtime logger configuration and file-handler lifecycle contracts."""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shared.events import runtime_logger


class RuntimeLoggerLifecycleTest(unittest.TestCase):
    def test_stream_mirror_failure_does_not_replace_logged_error_or_warning(self) -> None:
        logger = MagicMock()

        def fail_stream_emit(_event: dict) -> None:
            raise OSError("stream unavailable")

        with patch.object(runtime_logger, "get_logger", return_value=logger):
            runtime_logger.log_error(
                "test.component",
                "failed_operation",
                ValueError("original failure"),
                stream_emit=fail_stream_emit,
            )
            runtime_logger.log_warning(
                "test.component",
                "warning_operation",
                "original warning",
                stream_emit=fail_stream_emit,
            )

        self.assertEqual(1, logger.error.call_count)
        self.assertEqual(3, logger.warning.call_count)

    def test_existing_root_handler_does_not_skip_level_and_noise_policy(self) -> None:
        root = MagicMock()
        root.handlers = [MagicMock()]
        noisy_loggers = {
            name: MagicMock()
            for name in ("neo4j.notifications", "httpx", "httpcore")
        }

        def get_logger(name=None):
            return root if name is None else noisy_loggers[name]

        with (
            patch.object(runtime_logger.logging, "getLogger", side_effect=get_logger),
            patch.object(
                runtime_logger,
                "SETTINGS",
                SimpleNamespace(observability=SimpleNamespace(log_level="ERROR")),
            ),
        ):
            runtime_logger._configure_root_once()

        root.addHandler.assert_not_called()
        root.setLevel.assert_called_once_with(logging.ERROR)
        for logger in noisy_loggers.values():
            logger.setLevel.assert_called_once_with(logging.WARNING)

    def test_partial_file_handler_attach_failure_rolls_back_first_handler(self) -> None:
        root = MagicMock()
        first_handler = MagicMock(spec=logging.FileHandler)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(runtime_logger.logging, "getLogger", return_value=root),
                patch.object(
                    runtime_logger.logging,
                    "FileHandler",
                    side_effect=[first_handler, OSError("error log unavailable")],
                ),
                patch.object(
                    runtime_logger,
                    "SETTINGS",
                    SimpleNamespace(
                        observability=SimpleNamespace(
                            file_logging_enabled=True,
                            log_level="INFO",
                        )
                    ),
                ),
            ):
                with self.assertRaisesRegex(OSError, "error log unavailable"):
                    runtime_logger.attach_run_file_handlers(Path(directory))

        root.removeHandler.assert_called_once_with(first_handler)
        first_handler.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
