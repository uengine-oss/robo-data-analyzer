from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step8_post_processing.handoff_skip import log_handoff_skip


class HandoffSkipLoggingTest(unittest.TestCase):
    def test_user_stop_is_an_info_event_not_a_failure_warning(self) -> None:
        pause = PauseController()
        pause.request_stop()

        with (
            patch(
                "pipeline.steps.step8_post_processing.handoff_skip.log_info"
            ) as log_info,
            patch(
                "pipeline.steps.step8_post_processing.handoff_skip.log_warning"
            ) as log_warning,
        ):
            log_handoff_skip("step8.test", "후처리", pause)

        log_info.assert_called_once_with(
            "step8.test", "analysis_stopped", "사용자 중단 — 후처리 생략"
        )
        log_warning.assert_not_called()

    def test_actual_analysis_failure_remains_a_warning(self) -> None:
        pause = PauseController()

        with (
            patch(
                "pipeline.steps.step8_post_processing.handoff_skip.log_info"
            ) as log_info,
            patch(
                "pipeline.steps.step8_post_processing.handoff_skip.log_warning"
            ) as log_warning,
        ):
            log_handoff_skip("step8.test", "후처리", pause)

        log_warning.assert_called_once_with(
            "step8.test", "analysis_aborted", "분석 실패 — 후처리 생략"
        )
        log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
