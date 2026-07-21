"""Agent 미들웨어의 선택적 사후 진단 JSONL 기록 경계."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.events.runtime_logger import log_warning


def append_optional_diagnostic_event(
    output_path: Path,
    event: dict[str, Any],
    *,
    component: str,
) -> bool:
    """진단 이벤트를 append하고, 실패를 경고로 표면화하되 주 실행은 보존한다."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as output_file:
            output_file.write(
                json.dumps(event, ensure_ascii=False, default=str) + "\n"
            )
        return True
    except (OSError, TypeError, ValueError) as error:
        log_warning(
            component,
            "append_diagnostic_event",
            "보조 진단 JSONL 기록 실패 — 스트림과 Agent 실행은 계속",
            path=str(output_path),
            error=f"{type(error).__name__}: {error}",
        )
        return False
