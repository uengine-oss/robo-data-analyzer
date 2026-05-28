"""소스 확장자 → 언어 — 단일 정의처.

antlr-parser 가 실제로 emit 하는 언어만. Framework 언어는 ``ontology_roles.yaml``
의 roles 키와 일치해야 한다 (java / c / python). 새 언어 추가 = 이 dict 1줄.
"""
from __future__ import annotations

EXT_TO_LANG: dict[str, str] = {
    "java": "java",
    "c":    "c",
    "h":    "c",
    "py":   "python",
}
