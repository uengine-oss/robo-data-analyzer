"""표준 로깅 헬퍼 — silent fail 금지 + 일관된 출력 형식 + run-scope 파일 핸들러.

설계 원칙 (절대원칙 #3 + #6)
─────────────────────────────
- 모든 silent fail 자리에 ``log_error()`` / ``log_warning()`` 호출 → 사라지지 않음.
- 형식 통일: ``[LEVEL] component.func: message  key=value ...`` — grep 가능.
- 선택적 ``stream_emit`` 인자 — 프론트 NDJSON 스트림에도 동시 push.

런 단위 파일 핸들러 (`tail -f` 추적용)
──────────────────────────────────────
``execute_run`` 이 ``attach_run_file_handlers(run_dir)`` 로 두 파일 attach:

- ``<run_dir>/analyzer.log`` — root level (기본 INFO) 이상 전체.
- ``<run_dir>/error.log``    — WARNING 이상만 (사고 추적 전용).

``ROBO_LOG_FILE_ENABLED=false`` (운영기) 면 attach 자체 안 함 → I/O 0 / 성능 영향 0.
``delay=True`` 옵션으로 첫 write 시점에만 파일 open → 빈 run 의 빈 파일 X.
``run_executor`` 가 ``finally`` 에서 ``detach_run_file_handlers`` 로 close 보장
(Windows fd 충돌 회피 + FastAPI 멀티 run 동시 격리).

사용 예::

    from shared.logging_and_stream.logger import log_error, log_warning

    try:
        await dump_state_artifacts(run, state)
    except Exception as e:
        log_error("workflow_runner.artifact_dumper", "dump_state_artifacts", e,
                  stream_emit=stream.emit if stream else None,
                  run_id=run.run_id)
        raise   # 절대원칙 #3 — silent return 금지. 호출자가 결정.

새 컴포넌트 = ``component`` 인자에 모듈 경로 (예: ``"analysis_flow.step4_linking.stage1_regex_grep"``).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from shared.config.env_catalog import ENV


_LOG_FORMAT = "%(asctime)s %(levelname)-7s │ %(name)s │ %(message)s"
_DATE_FORMAT = "%H:%M:%S"

# run-scope 파일 핸들러 파일명 — 운영자 표준 (옛 base_analyzer 유산).
_ANALYZER_LOG_FILENAME = "analyzer.log"
_ERROR_LOG_FILENAME = "error.log"


# ── 한글 친화 라벨 매핑 (Formatter 가 사용 — 호출처는 영어 키 그대로 = grep 가능) ──
#
# 매 라벨 추가 시 이 dict 한 곳만 수정. 호출처 (log_info / log_warning / log_error)
# 의 `component` / `operation` 인자는 영어 그대로 (검색 / 호환 유지).
# 매핑 없으면 raw 영어 키 그대로 출력 (graceful fallback).

_COMPONENT_LABELS: dict[str, str] = {
    "run":                       "🚀 실행",
    "step1.parse_ddl":           "1️⃣  DDL 파싱",
    "step2.parse_source":        "2️⃣  소스 파싱",
    "step3.profile":             "3️⃣  Profile Agent",
    "step4.link_deterministic":  "4️⃣ ①결정론 연결",
    "step4.link_calls_by_llm":   "4️⃣ ②Call LLM 추출",
    "step4.link_calls_by_regex": "4️⃣ ②Call regex 추출",
    "step4.link_unresolved":     "4️⃣ ③Linking Agent",
    "step5.build_context":       "5️⃣  분석 컨텍스트",
    "step6.analyze_function":    "6️⃣  함수 분석",
    "step7.summarize_module":    "7️⃣  모듈 요약",
    "step7.enrich_table_description": "7️⃣  테이블 설명",
    "step7.extract_dml_metadata":     "7️⃣  DML 메타",
    "step7.enrich_metadata_from_catalog": "7️⃣  catalog 메타",
    "step7.enrich_lineage_from_catalog":  "7️⃣  catalog 리니지",
    "step7.embed":               "7️⃣  임베딩",
    "llm.call":                  "🤖 LLM 호출",
    "stream.phase":              "📡 단계",
    "stream.tool":               "🔧 도구",
    "stream.todo":               "📝 Todo",
    "stream.error":              "💥 스트림 에러",
    "stream.warning":            "⚠️  스트림 경고",
}

_OPERATION_LABELS: dict[str, str] = {
    "start":              "▶ 시작",
    "done":               "✓ 완료",
    "skip":               "⊘ 건너뜀",
    "complete":           "🎉 종료",
    "config_missing":     "⚠ 설정 누락",
    "neo4j_ready":        "✓ Neo4j 준비",
    "artifacts_dumped":   "✓ 산출물 저장",
    "dump_artifacts_failed": "✗ 산출물 저장 실패",
    "parse_file":         "  파일 파싱",
    "parsed":             "  파싱 완료",
    "upsert_schemas":     "  ↑ schemas",
    "upsert_tables":      "  ↑ tables",
    "upsert_columns":     "  ↑ columns",
    "upsert_has_column":  "  ↑ has_column",
    "upsert_belongs_to":  "  ↑ belongs_to",
    "upsert_fk":          "  ↑ fk",
    "scanning":           "  탐색 중",
    "scanned":            "  탐색 완료",
    "agent_invoke":       "  Agent 호출",
    "collecting_call_sites": "  call site 수집",
    "round_start":        "  ⤷ 라운드 시작",
    "round_done":         "  ⤶ 라운드 완료",
    "one_liner_start":    "  one-liner 시작",
    "one_liner_done":     "  one-liner 완료",
    "node_done":          "  ✓ 노드",
    "tool_start":         "  ▶ 도구",
    "tool_end":           "  ✓ 도구",
    "todo_update":        "  todo 변경",
}

_KEY_LABELS: dict[str, str] = {
    "project":              "프로젝트",
    "strategy":             "전략",
    "run_id":               "run",
    "thread_id":            "thread",
    "database":             "DB",
    "ddl_dir":              "DDL경로",
    "source_root":          "소스경로",
    "parser":               "파서",
    "sql_files":            "SQL파일",
    "parsed_files":         "파싱파일",
    "schemas":              "스키마",
    "tables":               "테이블",
    "columns":              "컬럼",
    "functions":            "함수",
    "modules":              "모듈",
    "has_column_edges":     "has_column",
    "belongs_to_edges":     "belongs_to",
    "fk_edges":             "fk",
    "parent_of_edges":      "parent_of",
    "uses_edges":           "uses",
    "reads_writes_edges":   "R/W",
    "calls_edges":          "calls",
    "refer_to_edges":       "refer_to",
    "unresolved_cases":     "미해결",
    "resolved":             "해결",
    "still_unresolved":     "남은미해결",
    "matched_skill":        "매칭SKILL",
    "confidence":           "신뢰도",
    "patterns":             "패턴수",
    "rounds":               "라운드수",
    "round":                "라운드",
    "in_cycle":             "순환내",
    "concurrency":          "동시성",
    "analyzed":             "분석됨",
    "failed":               "실패",
    "agent":                "Agent",
    "model":                "모델",
    "prompt_name":          "프롬프트",
    "input_tokens":         "입력토큰",
    "output_tokens":        "출력토큰",
    "cached_tokens":        "캐시토큰",
    "total_tokens":         "총토큰",
    "cost_usd":             "비용$",
    "elapsed_ms":           "소요ms",
    "elapsed_sec":          "소요s",
    "size_bytes":           "크기B",
    "file":                 "파일",
    "function_id":          "함수ID",
    "tool":                 "도구",
    "message":              "메시지",
    "in_progress":          "진행중",
    "done":                 "완료",
    "total":                "전체",
    "count":                "개수",
    "batch":                "배치",
    "call_sites":           "call수",
    "max_limit":            "한도",
    "internal":             "내부",
    "external":             "외부",
    "table_embedded":       "테이블임베드",
    "column_embedded":      "컬럼임베드",
    "function_embedded":    "함수임베드",
    "builder":              "빌더",
    "big_functions":        "큰함수",
    "threshold_tokens":     "임계토큰",
    "contexts":             "컨텍스트",
}


class _KoreanLabelFormatter(logging.Formatter):
    """component / operation / kv 키를 한글 라벨로 치환하는 Formatter.

    호출처 (log_info 등) 는 영어 키 그대로 사용 — 이 Formatter 가 출력 시점에만 치환.
    매핑 dict 에 없는 키는 raw 그대로 출력 (graceful fallback).

    출력 예::

        14:32:01 INFO    │ 1️⃣  DDL 파싱 │ ▶ 시작 프로젝트=apascrt SQL파일=1
        14:32:05 INFO    │ 🤖 LLM 호출 │ ✓ 완료 모델=qwen/qwen3.6 입력토큰=4500 비용$=0.001234 소요ms=8234
    """

    def format(self, record: logging.LogRecord) -> str:
        # name (component) 라벨 치환
        original_name = record.name
        record.name = _COMPONENT_LABELS.get(original_name, original_name)
        try:
            return super().format(record)
        finally:
            record.name = original_name


def _level_from_name(name: str, default: int = logging.INFO) -> int:
    """문자열 → logging 레벨 int. 알 수 없는 값은 default 로 fallback."""
    return getattr(logging, name.strip().upper(), default)


def _configure_root_once() -> None:
    """root logger 1회 설정 (idempotent).

    레벨은 ``ENV.log.level`` 사용 (env_catalog 단일 진입점).
    이미 핸들러 있으면 건너뜀 (uvicorn 기본 핸들러 보존).

    외부 라이브러리 노이즈 차단:
    - ``neo4j.notifications`` — 매 run 마다 ``Schema.IndexOrConstraintAlreadyExists`` 12 줄 도배.
    - ``httpx`` — 매 LLM 호출마다 ``HTTP Request: POST ... 200 OK`` (중복).
    - ``httpcore`` — httpx 보조.
    → WARNING 이상만 root 로 통과 (실제 에러는 그대로 보임).
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_KoreanLabelFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)
    root.setLevel(_level_from_name(ENV.log.level))

    # 외부 라이브러리 노이즈 차단 — INFO 줄 안 흘러옴, 실제 에러만 통과.
    for noisy in ("neo4j.notifications", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_root_once()


# ── run-scope 파일 핸들러 (analyzer.log + error.log) ─────────

def attach_run_file_handlers(run_dir: Path) -> list[logging.FileHandler]:
    """``<run_dir>/analyzer.log`` + ``<run_dir>/error.log`` 핸들러 attach.

    ``ENV.log.file_enabled=False`` 이면 attach 안 함 (성능 영향 0 / 디스크 I/O 0) —
    빈 리스트 반환. 호출자는 결과 length 0 이면 detach 시 자동 no-op.

    ``delay=True`` 로 핸들러 생성 — 첫 ``emit()`` 시점에 파일 open. 빈 run 은 빈 파일 X.

    Args:
        run_dir: ``run.run_dir`` — 이미 ``mkdir`` 된 디렉토리. 호출자가 책임.

    Returns:
        attach 한 핸들러 리스트 — ``detach_run_file_handlers`` 의 입력.
    """
    if not ENV.log.file_enabled:
        return []

    root = logging.getLogger()
    root_level = _level_from_name(ENV.log.level)

    # 파일 핸들러도 한글 Formatter 사용 — analyzer.log / error.log 의 가독성 통일.
    file_formatter = _KoreanLabelFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # analyzer.log — root 레벨 (INFO 기본) 이상 전체
    main_handler = logging.FileHandler(
        run_dir / _ANALYZER_LOG_FILENAME, mode="a", encoding="utf-8", delay=True,
    )
    main_handler.setLevel(root_level)
    main_handler.setFormatter(file_formatter)
    root.addHandler(main_handler)

    # error.log — WARNING 이상만 (사고 추적용)
    error_handler = logging.FileHandler(
        run_dir / _ERROR_LOG_FILENAME, mode="a", encoding="utf-8", delay=True,
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(file_formatter)
    root.addHandler(error_handler)

    return [main_handler, error_handler]


def detach_run_file_handlers(handlers: list[logging.FileHandler]) -> None:
    """``attach_run_file_handlers`` 가 반환한 핸들러들 remove + close.

    ``finally`` 블록에서 호출 — close 안 하면 Windows 에서 stale fd 가 다음 run 의
    ``rmtree`` (``_reset_latest_dirs``) 를 권한 오류로 실패시킴.

    빈 리스트 (file_enabled=False 였을 때) 는 no-op.
    """
    root = logging.getLogger()
    for handler in handlers:
        try:
            root.removeHandler(handler)
        finally:
            # close() 가 IOError 던져도 다음 핸들러는 계속 처리
            try:
                handler.close()
            except Exception:  # noqa: BLE001 — close 실패가 run 자체를 막아선 안 됨
                pass


def get_logger(component: str) -> logging.Logger:
    """모듈별 로거 (component = 모듈 경로)."""
    return logging.getLogger(component)


def _format_kv(kwargs: dict[str, Any]) -> str:
    """key=value ... 한글 라벨로 직렬화 (None 값은 생략).

    ``_KEY_LABELS`` 매핑에 없는 키는 영어 그대로 (graceful fallback).
    """
    if not kwargs:
        return ""
    parts = []
    for k, v in kwargs.items():
        if v is None:
            continue
        label = _KEY_LABELS.get(k, k)
        parts.append(f"{label}={v}")
    return " " + " ".join(parts) if parts else ""


def _label_operation(operation: str) -> str:
    """operation 이름 → 한글 라벨 (매핑 없으면 원래 이름)."""
    return _OPERATION_LABELS.get(operation, operation)


def log_error(
    component: str,
    operation: str,
    error: BaseException | str,
    stream_emit: Optional[Callable[[dict], None]] = None,
    **context: Any,
) -> None:
    """오류 로깅 — stderr / file handler / (선택) NDJSON 스트림.

    ``error`` 가 ``BaseException`` 이면 **stderr / error.log 에 traceback 자동 출력**
    (``logger.error(..., exc_info=...)``). ``__cause__`` chain 도 자동 따라감.
    NDJSON 스트림 / analyzer.log 의 한 줄에는 type+msg 만 박혀 노이즈 없음.

    Args:
        component: 모듈 경로 (예: ``"analysis_flow.step6_analyze_function"``).
        operation: 함수/작업 이름 (예: ``"analyze_function_three_split"``).
        error: Exception 또는 메시지 문자열.
        stream_emit: NDJSON 스트림 emit 함수 (없으면 file handler / stderr 만).
        **context: 추가 컨텍스트 (run_id / function_id 등).
    """
    if isinstance(error, BaseException):
        error_str = f"{type(error).__name__}: {error}"
        exc_info = (type(error), error, error.__traceback__)
    else:
        error_str = str(error)
        exc_info = None

    kv = _format_kv(context)
    msg = f"{_label_operation(operation)}: {error_str}{kv}"
    # exc_info 가 있으면 logging 이 자동으로 traceback + __cause__ chain 출력.
    get_logger(component).error(msg, exc_info=exc_info)

    if stream_emit is not None:
        stream_emit({
            "type": "error",
            "component": component,
            "operation": operation,
            "message": error_str,
            **context,
        })


def log_warning(
    component: str,
    operation: str,
    message: str,
    stream_emit: Optional[Callable[[dict], None]] = None,
    **context: Any,
) -> None:
    """경고 로깅 — silent skip 자리에 사용 (의심스러운 fallback 등).

    Args:
        component: 모듈 경로.
        operation: 작업 이름.
        message: 경고 메시지.
        stream_emit: 옵션 — NDJSON 동시 push.
        **context: 추가 컨텍스트.
    """
    kv = _format_kv(context)
    get_logger(component).warning(f"{_label_operation(operation)}: {message}{kv}")

    if stream_emit is not None:
        stream_emit({
            "type": "warning",
            "component": component,
            "operation": operation,
            "message": message,
            **context,
        })


def log_info(
    component: str,
    operation: str,
    message: str = "",
    **context: Any,
) -> None:
    """일반 진행 로그 (성공 이벤트 / 통계 / 진행 milestone).

    형식 (analyzer.log)::

        [INFO] step1.parse_ddl: start project=apascrt strategy=framework
        [INFO] step1.parse_ddl: done schemas=1 tables=7 columns=73 elapsed=0.3s
        [INFO] step4.link_calls_by_llm: batch 1/3 calls=30 elapsed=4.1s
        [INFO] llm.call: done in=4500 out=850 cached=4400 cost=$0.0014 elapsed=8.2s

    Args:
        component: 모듈 / step 식별자 (예: ``"step1.parse_ddl"`` / ``"llm.call"``).
        operation: 진행 이벤트 이름 (``"start"`` / ``"done"`` / ``"batch"`` / ``"round"`` 등).
        message: 추가 설명 (선택, 기본 빈 문자열 — 통계만 충분할 때 생략).
        **context: 통계 / 컨텍스트 (개수 / cost / elapsed_sec 등).
    """
    kv = _format_kv(context)
    op_label = _label_operation(operation)
    body = f"{message}{kv}" if message else kv.lstrip()
    line = f"{op_label}: {body}" if body else op_label
    get_logger(component).info(line)
