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
from shared.config.settings import SETTINGS
from pipeline.languages import ROUTINE_LABELS, role_of
from pipeline.languages import DBMS_EXTS, EXT_TO_LANG
from graph.schema.node_ids import child_id, code_file_id, code_ns_id, positioned
from shared.events.runtime_logger import log_error, log_warning

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

def ast_and_source_roots(
    source_path: Path,
    corpus_root: Path | None = None,
) -> tuple[Path, Path]:
    """``project.root`` → ``(AST 루트, 소스 루트)``.

    ``corpus_root``가 있으면 그 실행의 source/AST가 반드시 같은 corpus에 속하며,
    없을 때만 설정의 canonical data root를 사용한다. 명시한 corpus 밖 source는 서로 다른
    실행의 source와 AST를 섞지 않도록 실패시킨다.

    소스 루트 = AST 의 ``filePath`` 가 상대로 매달린 **antlr 파싱 base** (``_src_path``
    가 ``source_root / filePath`` 로 원문 슬라이싱). 이 데이터 규약(``data/source/<project>``
    + AST ``filePath="<project>/file"``)에서 base 는 항상 ``data/source``:
    - ``project.root == data/source`` (HTTP 업로드·일괄) → 소스루트=``data/source``,
      AST=``data/analysis`` 전체.
    - ``project.root == data/source/<project>`` (CLI 단일 대상) → 소스루트=``data/source``
      (base 복원 — 이중경로 차단), AST=``data/analysis/<project>`` 로 스코프(그 프로젝트만).
    - 설정의 canonical corpus를 쓰는 호출에서만 ``data/source`` 밖 Electron 경로모드를
      허용한다. 이때 소스루트=선택 폴더, AST=canonical ``data/analysis`` 전체다.
    """
    data_root = (
        Path(corpus_root).expanduser().resolve()
        if corpus_root is not None
        else SETTINGS.analysis_data.dir.resolve()
    )
    analysis_root = data_root / "analysis"
    source_base = (data_root / "source").resolve()
    root = source_path.resolve()
    try:
        rel = root.relative_to(source_base)
    except ValueError:
        if corpus_root is not None:
            raise ValueError(
                f"source가 명시 corpus_root 밖에 있다: source={root}, corpus={data_root}"
            )
        return analysis_root, root           # 경로모드 — base = 고른 폴더 그대로
    if rel == Path("."):
        return analysis_root, source_base    # 업로드·일괄 — 전체
    return analysis_root / rel, source_base   # CLI 단일 대상 — 그 프로젝트로 스코프


def read_antlr_json(json_path: Path, source_root: Path) -> ParseResult:
    """antlr JSON 파일 1개 → ParseResult.

    Args:
        json_path: antlr-parser 가 만든 ``*.json`` (루트 노드 = ``FILE``).
        source_root: 원본 소스 디렉토리 (``data/source``) — JSON 의 ``filePath`` 를
            절대 소스 경로로 풀어 ``file_path``에 박는다(step7 본문 슬라이싱용).

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
        result = _read_dbms(raw, source_root, json_path)
        source_language = _parser_source_language(raw, source_root)
        for node in result.code_nodes:
            if source_language:
                node.extra["source_language"] = source_language
        return result

    lang = EXT_TO_LANG.get(ext)
    if lang is None:
        log_warning(
            _COMPONENT, "read_antlr_json",
            "지원하지 않는 확장자 — 건너뜀 (antlr-parser 미지원 언어)",
            json_path=str(json_path), ext=ext,
        )
        return ParseResult()

    result = _read_framework(raw, lang, source_root, json_path)
    if result.unknown_nodes:
        counts: dict[str, int] = {}
        for unknown in result.unknown_nodes:
            counts[unknown["type"]] = counts.get(unknown["type"], 0) + 1
        log_warning(
            _COMPONENT, "unknown_node_types",
            "ontology 미등록 AST 타입 — 자식 사실은 보존했고 타입 분류는 지원 한계",
            json_path=str(json_path), lang=lang, counts=counts,
            samples=[f"{u['type']}@{u['line']}" for u in result.unknown_nodes[:5]],
        )
    return result


def _parser_source_language(raw: dict, source_root: Path) -> str:
    """Read the parser-owned SQL dialect from its immutable diagnostic sidecar.

    ``.sql`` is shared by Oracle and PostgreSQL. Guessing here would duplicate the parser's
    routing and can silently assign a procedural variable as a physical column. The parser
    already records its selected ``language`` beside every AST, so consume that fact and fail
    closed when the sidecar is absent or inconsistent.
    """
    file_path = str(raw.get("filePath") or "").strip().replace("\\", "/")
    if not file_path:
        return ""
    diagnostics_root = (source_root.parent / "diagnostics").resolve()
    diagnostic_path = (diagnostics_root / f"{file_path}.parse.json").resolve()
    try:
        diagnostic_path.relative_to(diagnostics_root)
    except ValueError:
        log_warning(
            _COMPONENT,
            "parser_diagnostic_outside_root",
            "parser diagnostic 경로가 corpus diagnostics 밖을 가리켜 방언 근거를 폐기",
            file_path=file_path,
        )
        return ""
    if not diagnostic_path.is_file():
        log_warning(
            _COMPONENT,
            "parser_diagnostic_missing",
            "공유 .sql 방언을 확정할 parser diagnostic이 없어 방언 의존 근거를 비활성화",
            diagnostic_path=str(diagnostic_path),
        )
        return ""
    try:
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        log_warning(
            _COMPONENT,
            "parser_diagnostic_invalid",
            f"parser diagnostic을 읽지 못해 방언 의존 근거를 비활성화: {error}",
            diagnostic_path=str(diagnostic_path),
        )
        return ""
    diagnostic_source = str(diagnostic.get("sourcePath") or "").replace("\\", "/")
    language = str(diagnostic.get("language") or "").strip().casefold()
    if diagnostic_source != file_path or language not in {"oracle", "postgresql"}:
        log_warning(
            _COMPONENT,
            "parser_diagnostic_mismatch",
            "parser diagnostic의 sourcePath/language가 AST와 일치하지 않아 방언 근거를 폐기",
            diagnostic_path=str(diagnostic_path),
            ast_source=file_path,
            diagnostic_source=diagnostic_source,
            language=language,
        )
        return ""
    return language


# ── Framework (java / c / python) ─────────────────────────────
