"""antlr JSON AST → 코드 노드 + 분석 단서.

antlr-parser 가 만든 ``data/analysis/target/<p>/*.json`` 트리만 읽는다 — 소스 재파싱 X.
Framework / DBMS 공용 단일 reader (언어 해석은 ontology 가 흡수).

생산물 (ParseResult):
- Module / CodeNode / Package / Variable / Constant / DataType 노드
- PARENT_OF (트리 중첩) 엣지
- call_sites (FUNCTION_CALL) · import_refs (INCLUDE/IMPORT) — 노드 아님, 후속 step 단서

노드 라벨 = 자기 antlr ``type`` 하나 (reader 가 ``node["type"]`` 직결, Framework/DBMS 공통).
ontology ``role_of`` 는 분류 전용 — 어느 dataclass/엣지/internal 인지만 정한다 (라벨엔 안 씀).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from graph.schema.graph_edges import (
    BelongsTo,
    HasMember,
    ParentOf,
    StatementNext,
)
from graph.schema.graph_nodes import Constant, DataType, CodeNode, Module, Package, Variable
from analyzer_settings import SETTINGS
from pipeline.languages import ROUTINE_LABELS, role_of
from pipeline.languages import DBMS_EXTS, EXT_TO_LANG
from graph.schema.node_ids import child_id, code_file_id, code_ns_id, positioned
from runtime_events.runtime_logger import log_error, log_warning

_COMPONENT = "pipeline.steps.step2_load_ast.antlr_json_reader"

# DBMS 순수 선언 노드 — Variable 노드로 수집 (CodeNode 아님, 분석 제외).
# ★ CURSOR 정의는 제외 — 쿼리 로직(선별 조건 = 비즈니스 규칙)을 담으므로 CodeNode 로 분석한다
#   (자식 SELECT 요약 → 커서가 톱레벨 문장으로 규칙 추출). DECLARE(skip) 안이라 유효부모=루틴.
_DBMS_VAR_TYPES: frozenset[str] = frozenset({
    "PARAMETER", "VARIABLE", "PACKAGE_VARIABLE",
})

# (옛 ``_DBMS_EXTS`` / ``_ROUTINE_LABELS`` 삭제 — 각각 ``source_extension.DBMS_EXTS`` 와
#  ``ontology.ROUTINE_LABELS`` 가 단일 정의처다. 종전엔 여기와 batch_analyze 가 목록을
#  따로 들어 실제로 갈라졌다: batch_analyze 쪽엔 METHOD 가 빠져 있었다. spec 046 FR-003/FR-005.)


from pipeline.steps.step2_load_ast.node_builders import ParseResult
from pipeline.steps.step2_load_ast.framework_reader import _read_framework
from pipeline.steps.step2_load_ast.dbms_reader import _read_dbms

def ast_and_source_roots(source_path: Path) -> tuple[Path, Path]:
    """``project.root`` → ``(AST 루트, 소스 루트)``.

    소스 루트 = AST 의 ``filePath`` 가 상대로 매달린 **antlr 파싱 base** (``_src_path``
    가 ``source_root / filePath`` 로 원문 슬라이싱). 이 데이터 규약(``data/source/<project>``
    + AST ``filePath="<project>/file"``)에서 base 는 항상 ``data/source``:
    - ``project.root == data/source`` (HTTP 업로드·일괄) → 소스루트=``data/source``,
      AST=``data/analysis`` 전체.
    - ``project.root == data/source/<project>`` (CLI 단일 대상) → 소스루트=``data/source``
      (base 복원 — 이중경로 차단), AST=``data/analysis/<project>`` 로 스코프(그 프로젝트만).
    - ``data/source`` 밖 (Electron 경로모드, 사용자가 고른 로컬 폴더) → 소스루트=그 폴더,
      AST=``data/analysis`` 전체 (antlr 가 그 구조로 미러).
    """
    analysis_root = SETTINGS.analysis_data.dir / "analysis"
    source_base = (SETTINGS.analysis_data.dir / "source").resolve()
    root = source_path.resolve()
    try:
        rel = root.relative_to(source_base)
    except ValueError:
        return analysis_root, root           # 경로모드 — base = 고른 폴더 그대로
    if rel == Path("."):
        return analysis_root, source_base    # 업로드·일괄 — 전체
    return analysis_root / rel, source_base   # CLI 단일 대상 — 그 프로젝트로 스코프


def read_antlr_json(json_path: Path, source_root: Path) -> ParseResult:
    """antlr JSON 파일 1개 → ParseResult.

    Args:
        json_path: antlr-parser 가 만든 ``*.json`` (루트 노드 = ``FILE``).
        source_root: 원본 소스 디렉토리 (``data/source``) — JSON 의 ``filePath`` 를
            절대 소스 경로로 풀어 ``file_path`` 에 박는다 (step6 본문 슬라이싱용).

    Returns:
        ParseResult. JSON 파싱 실패 시 빈 결과 (오류 로깅 후 — 다른 파일은 계속).
    """
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        # antlr JSON 은 정상이라면 항상 유효 — 깨졌다면 antlr-parser 산출물 문제.
        # 절대원칙 #3: silent 금지. 오류 기록 후 이 파일만 건너뜀.
        log_error(_COMPONENT, "read_antlr_json", e, json_path=str(json_path))
        return ParseResult()

    if not isinstance(raw, dict):
        log_warning(
            _COMPONENT, "read_antlr_json",
            "JSON 루트가 객체(FILE 노드)가 아님 — 건너뜀",
            json_path=str(json_path),
        )
        return ParseResult()

    file_name = raw.get("fileName") or json_path.name
    ext = Path(file_name).suffix.lstrip(".").lower()

    if ext in DBMS_EXTS:
        return _read_dbms(raw, source_root, json_path)

    lang = EXT_TO_LANG.get(ext)
    if lang is None:
        log_warning(
            _COMPONENT, "read_antlr_json",
            "지원하지 않는 확장자 — 건너뜀 (antlr-parser 미지원 언어)",
            json_path=str(json_path), ext=ext,
        )
        return ParseResult()

    return _read_framework(raw, lang, source_root, json_path)


# ── Framework (java / c / python) ─────────────────────────────
