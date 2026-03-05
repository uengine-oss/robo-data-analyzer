"""exp 파이프라인 전용 로깅: 터미널 가독성용 짧은 태그 포맷터."""

from __future__ import annotations

import logging
import re

# logger name -> 콘솔용 짧은 태그
NAME_TO_SHORT = {
    "exp.pipeline": "RUN",
    "exp.run": "RUN",
    "exp.steps.step2_java_ast": "STEP2",
    "exp.steps.step3_artifacts": "STEP3",
    "exp.steps.step4_index": "STEP4",
    "exp.steps.step5_linking": "STEP5",
    "exp.steps.step6_summary": "STEP6",
    "exp.chunk_bundle": "CHUNK",
    "exp.call_batch": "CALL",
    "exp.artifact_dump": "DUMP",
    "exp.agent.deepagent": "AGENT",
}

# exp.steps.stepN_* 패턴 -> STEPN
_STEP_PATTERN = re.compile(r"^exp\.steps\.step(\d+)_\w+$")


def _short_name(name: str) -> str:
    if name in NAME_TO_SHORT:
        return NAME_TO_SHORT[name]
    m = _STEP_PATTERN.match(name)
    if m:
        return f"STEP{m.group(1)}"
    if name.startswith("exp."):
        # exp.neo4j.writer -> NEO4J, exp.config -> CONFIG
        parts = name.split(".")
        if len(parts) >= 2:
            return parts[1].upper()[:8]
    return name.split(".")[-1].upper()[:10] if name else "EXP"


def _strip_exp_tag(message: str) -> str:
    """메시지 앞 [EXP:STEPn] / [EXP:RUN] 등 중복 접두어 제거."""
    if not message or not message.strip():
        return message
    # [EXP:STEP5] [섹션] ... -> [섹션] ...
    s = message.strip()
    for prefix in ("[EXP:RUN]", "[EXP:STEP2]", "[EXP:STEP3]", "[EXP:STEP4]", "[EXP:STEP5]", "[EXP:STEP6]", "[EXP:CHUNK]", "[EXP:CALL]", "[EXP:DEEPAGENT]", "[EXP:DEBUG]"):
        if s.startswith(prefix):
            s = s[len(prefix):].lstrip()
            break
    return s


class ShortNameFilter(logging.Filter):
    """LogRecord에 short_name 속성 추가."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.short_name = _short_name(record.name)
        return True


class ExpConsoleFormatter(logging.Formatter):
    """콘솔용: 짧은 태그 + 메시지 내 [EXP:...] 중복 제거."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "short_name"):
            record.short_name = _short_name(record.name)
        msg = record.getMessage()
        msg = _strip_exp_tag(msg)
        # WARNING/ERROR 시 앞에 레벨 강조
        level = record.levelname
        if level == "WARNING":
            prefix = "[WARN] "
        elif level == "ERROR":
            prefix = "[ERROR] "
        else:
            prefix = ""
        return f"{prefix}[{record.short_name}] {msg}"
