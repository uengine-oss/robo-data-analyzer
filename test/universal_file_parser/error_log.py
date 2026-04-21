"""DeepAgent 파싱 실패 시 error.log 작성

파싱이 최종 실패했을 때, 사람이 나중에 진단할 수 있도록
모든 시도 이력을 한 파일에 누적 기록한다.

위치: c:/tmp/universal_parser_error.log
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


ERROR_LOG_PATH = "c:/tmp/universal_parser_error.log"


@dataclass
class AttemptRecord:
    """DeepAgent 의 한 번의 시도 기록."""
    version: int                        # v1, v2, v3, ...
    phase: str                          # 'ast' | 'exec' | 'validate' | 'submit'
    status: str                         # 'ok' | 'failed'
    error_type: str | None = None       # 'forbidden_import', 'KeyError', 'low_coverage' ...
    error_message: str | None = None
    signature: str | None = None        # 해시 — 같은 에러 판별용
    code_preview: str | None = None     # 생성된 코드 앞부분 (있으면)


def write_error_log(path: str, data_size: int, state, fmt_hint: str = "") -> None:
    """최종 실패 시 error.log 에 상세 기록 append.

    Args:
        path: 파싱 시도한 원본 파일 경로
        data_size: 파일 크기 (bytes)
        state: CodegenState (attempts, error_signatures, termination_reason 포함)
        fmt_hint: 포맷 힌트 (detect 가 뱉은 fmt)
    """
    try:
        Path(ERROR_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)

        lines = _format_entry(path, data_size, state, fmt_hint)
        text = "\n".join(lines) + "\n\n"

        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        # error.log 작성 실패는 조용히 무시 (원본 파싱 결과에 영향 주지 않도록)
        pass


def _format_entry(path: str, data_size: int, state, fmt_hint: str) -> list[str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_name = os.path.basename(path)

    lines = [
        "=" * 80,
        f"[{now}] {file_name} — UNPARSEABLE",
        "=" * 80,
        f"경로:       {path}",
        f"크기:       {data_size:,} B",
        f"포맷 추정:  {fmt_hint or 'unknown'}",
        f"총 시도:    {len(state.attempts)}",
        f"종료 사유:  {state.termination_reason or 'unknown'}",
        "",
    ]

    # 시도별 이력
    if state.attempts:
        lines.append("── 시도 이력 ──")
        for attempt in state.attempts:
            lines.extend(_format_attempt(attempt))
        lines.append("")

    # 마지막 코드 (있으면)
    last_code = getattr(state, "last_code", None)
    if last_code:
        lines.append("── 마지막 생성 코드 ──")
        lines.append(last_code)

    return lines


def _format_attempt(a: AttemptRecord) -> list[str]:
    status_icon = "✅" if a.status == "ok" else "❌"
    header = f"  [v{a.version}] {status_icon} phase={a.phase:<8} status={a.status}"
    out = [header]
    if a.error_type:
        out.append(f"          error_type: {a.error_type}")
    if a.error_message:
        preview = a.error_message[:200] + ("..." if len(a.error_message) > 200 else "")
        out.append(f"          message:    {preview}")
    if a.signature:
        out.append(f"          signature:  {a.signature}")
    return out
