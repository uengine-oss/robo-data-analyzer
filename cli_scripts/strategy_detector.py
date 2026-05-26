"""프로젝트 폴더의 파일 확장자로 strategy(framework / dbms) 자동 감지.

CLI 의 strategy 자동 결정 단일 진실 — `analyze_data_dir.py` / `baseline_runner.py`
두 곳에서 같이 import. 함수가 두 곳에 따로 있으면 drift 위험.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal


# DBMS 로 인식할 확장자.
_DBMS_EXTS: frozenset[str] = frozenset({".sql", ".pkb", ".pks", ".pls", ".ddl"})

# Framework 코드로 인식할 확장자.
_FRAMEWORK_EXTS: frozenset[str] = frozenset({
    ".java", ".kt", ".py", ".go", ".c", ".cpp", ".h", ".hpp",
    ".ts", ".js", ".rb", ".rs", ".php", ".scala",
})


def detect_strategy(project_dir: Path) -> Literal["framework", "dbms"]:
    """프로젝트 폴더의 파일 확장자 → ``"framework"`` 또는 ``"dbms"``.

    Framework 코드가 하나라도 있으면 framework. 없고 DBMS 확장자만 있으면 dbms.
    둘 다 없으면 framework (안전 기본값 — pipeline 분기 0).
    """
    has_framework = False
    has_dbms = False
    for f in project_dir.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in _FRAMEWORK_EXTS:
            has_framework = True
        elif ext in _DBMS_EXTS:
            has_dbms = True
    if has_framework:
        return "framework"
    if has_dbms:
        return "dbms"
    return "framework"
