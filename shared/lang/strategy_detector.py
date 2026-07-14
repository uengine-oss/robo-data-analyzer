"""프로젝트 폴더의 파일 확장자로 strategy(framework / dbms) 자동 감지.

CLI 의 strategy 자동 결정 단일 진실 — `analyze_data_dir.py` / `baseline_runner.py`
두 곳에서 같이 import.

★ 확장자 목록은 **여기 없다** — ``source_extension`` 이 유일한 정의처다 (헌법 III / spec 046
FR-003). 종전엔 여기가 자기 목록(14종)을 따로 들어 실제 파싱이 아는 목록(4종)과 갈라졌고,
그 결과 ``.kt`` 만 있는 프로젝트가 framework 로 판정된 뒤 전 파일 스킵 → **0노드 성공**.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from shared.lang.source_extension import DBMS_SUFFIXES, FRAMEWORK_SUFFIXES


class NoSupportedSourceError(RuntimeError):
    """분석 가능한 소스가 하나도 없다 — 0노드 "성공" 대신 명시적 실패 (헌법 IV / FR-003)."""


def detect_strategy(project_dir: Path) -> Literal["framework", "dbms"]:
    """프로젝트 폴더의 파일 확장자 → ``"framework"`` 또는 ``"dbms"``.

    Framework 코드가 하나라도 있으면 framework. 없고 DBMS 확장자만 있으면 dbms.
    **둘 다 없으면 ``NoSupportedSourceError``** — 옛 코드는 조용히 "framework" 를 골랐고,
    그러면 파서가 전 파일을 스킵해 **노드 0개짜리 run 이 성공으로 끝났다**(FR-003).
    """
    has_framework = False
    has_dbms = False
    for f in project_dir.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in FRAMEWORK_SUFFIXES:
            has_framework = True
        elif ext in DBMS_SUFFIXES:
            has_dbms = True
    if has_framework:
        return "framework"
    if has_dbms:
        return "dbms"
    raise NoSupportedSourceError(
        f"분석 가능한 소스가 없다: {project_dir} — "
        f"지원 확장자 = {sorted(FRAMEWORK_SUFFIXES | DBMS_SUFFIXES)} "
        "(shared/lang/source_extension.py 가 단일 정의처)."
    )
