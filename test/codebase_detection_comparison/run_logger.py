"""실행 과정을 파일 로그로 기록.

각 run 마다 logs/run_YYYYMMDD_HHMMSS_<target>.log 파일 생성.
Naive 호출, Deep Agent 의 도구 호출 시퀀스, 결과를 모두 포함.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).parent
LOGS_DIR = MODULE_ROOT / "logs"


class RunLogger:
    """단일 비교 실행의 전체 과정을 텍스트 + JSON 파일로 기록."""

    def __init__(self, target_name: str):
        LOGS_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = target_name.replace("/", "_").replace(":", "").replace(" ", "_")
        self.base_name = f"run_{ts}_{safe_name}"
        self.txt_path = LOGS_DIR / f"{self.base_name}.log"
        self.json_path = LOGS_DIR / f"{self.base_name}.json"

        self.target_name = target_name
        self.records: list[dict] = []
        self.started_at = ts

        # 시작 헤더.
        with self.txt_path.open("w", encoding="utf-8") as f:
            f.write(f"=== RUN LOG ===\n")
            f.write(f"target : {target_name}\n")
            f.write(f"started: {ts}\n")
            f.write("=" * 80 + "\n")

    def log(self, category: str, message: str, data: Any = None) -> None:
        """한 줄 로깅. 텍스트 + 누적 레코드 양쪽 저장."""

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        record = {"time": ts, "category": category, "message": message}
        if data is not None:
            record["data"] = _serialize(data)
        self.records.append(record)

        with self.txt_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{category:>12}] {message}\n")
            if data is not None:
                preview = json.dumps(_serialize(data), ensure_ascii=False, indent=2)
                if len(preview) > 2000:
                    preview = preview[:2000] + "\n... (truncated)"
                for line in preview.splitlines():
                    f.write(f"                {line}\n")

    def log_phase(self, phase: str, message: str) -> None:
        """Phase 전환 로그 (눈에 띄게 구분)."""

        with self.txt_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "-" * 80 + "\n")
            f.write(f"### {phase}: {message}\n")
            f.write("-" * 80 + "\n")
        self.log("PHASE", f"{phase}: {message}")

    def log_tool_call(self, index: int, tool_name: str, args: str, result_preview: str) -> None:
        """DeepAgent 도구 호출 로그."""

        msg = f"#{index:>2} {tool_name}({args[:80]})"
        self.log("TOOL_CALL", msg, {"args_full": args, "result_preview": result_preview})

    def log_result(self, method: str, result: Any) -> None:
        """최종 결과 저장."""

        data = _serialize(result)
        self.log("RESULT", f"{method} 최종 결과", data)

    def close(self, summary: dict | None = None) -> None:
        """실행 종료 시 호출. JSON 요약 파일 생성."""

        ended = datetime.now().strftime("%Y%m%d_%H%M%S")
        with self.txt_path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"=== END ===\n")
            f.write(f"ended: {ended}\n")
            if summary:
                f.write("\n[SUMMARY]\n")
                for k, v in summary.items():
                    f.write(f"  {k}: {v}\n")

        with self.json_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "target": self.target_name,
                    "started": self.started_at,
                    "ended": ended,
                    "summary": summary or {},
                    "records": self.records,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )


def _serialize(obj: Any) -> Any:
    """dataclass, Path, set 등을 JSON 가능한 형태로 변환."""

    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj
