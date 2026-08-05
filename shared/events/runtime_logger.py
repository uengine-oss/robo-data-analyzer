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

``ROBO_RUNTIME_EVENT_FILE_ENABLED=false`` (운영기) 면 attach 자체 안 함 → I/O 0 / 성능 영향 0.
``delay=True`` 옵션으로 첫 write 시점에만 파일 open → 빈 run 의 빈 파일 X.
``executor`` 가 ``finally`` 에서 ``detach_run_file_handlers`` 로 close 보장
(Windows fd 충돌 회피 + FastAPI 멀티 run 동시 격리).

사용 예::

    from shared.events.runtime_logger import log_error, log_warning

    try:
        await upsert_nodes(neo, nodes)
    except Exception as e:
        log_error("graph.node_upsert", "upsert_nodes", e,
                  stream_emit=stream.emit if stream else None,
                  run_id=run.run_id)
        raise   # 절대원칙 #3 — silent return 금지. 호출자가 결정.

새 컴포넌트 = ``component`` 인자에 모듈 경로 (예: ``"pipeline.steps.step5_linking.link_tables"``).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from shared.config.settings import SETTINGS


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
    "step2.load_ast":            "2️⃣  AST 적재",
    "step3.load_external":       "3️⃣  외부 파일 적재",
    "step4.scan":             "4️⃣  Scan Agent",
    "step4.scan_external":       "4️⃣  외부 포맷 Agent",
    "step5.link_deterministic":  "5️⃣ ①결정론 연결",
    "step5.link_calls_by_name":  "5️⃣ ②Call 이름 분류",
    "step5.link_calls_by_ast":   "5️⃣ ②Call AST 분류",
    "step5.link_residual_patterns": "5️⃣ ③잔여 패턴 루프",
    "step5.link_external":       "5️⃣  외부 파일 연결",
    "step6.build_context":       "6️⃣  분석 컨텍스트",
    "step1.discover_datasource": "1️⃣  데이터소스 발견",
    "step7.analyze_code":    "7️⃣  코드노드 분석",
    "step8.summarize_module":    "8️⃣  모듈 요약",
    "step8.enrich_table_description": "8️⃣  테이블 설명",
    "step5.link_dbms_tables":         "5️⃣ ①DBMS 테이블 매칭",
    "step8.enrich_lineage_from_catalog":  "8️⃣  catalog 리니지",
    "step8.embed":               "8️⃣  임베딩",
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
    "round_stop":         "  ⊘ 라운드 종료",
    "analysis_stopped":   "■ 사용자 중단",
    "analysis_aborted":   "✗ 분석 실패",
    "guardrail_applied":  "🛡 가드레일 적용",
    "guardrail_observed": "🛡 가드레일 관측",
    "recovery_started":   "↻ 복구 시작",
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
    "database":             "DB",
    "ddl_dir":              "DDL경로",
    "source_root":          "소스경로",
    "parser":               "파서",
    "sql_files":            "SQL파일",
    "parsed_files":         "파싱파일",
    "schemas":              "스키마",
    "tables":               "테이블",
    "columns":              "컬럼",
    "code_nodes":            "코드노드",
    "modules":              "모듈",
    "has_column_edges":     "has_column",
    "belongs_to_edges":     "belongs_to",
    "fk_edges":             "fk",
    "parent_of_edges":      "parent_of",
    "reads_writes_edges":   "R/W",
    "calls_edges":          "calls",
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
    "input_tokens":         "입력",
    "output_tokens":        "출력",
    "cached_tokens":        "캐시",
    "total_tokens":         "총토큰",
    "cost_usd":             "비용",     # _humanize 가 "cost" 로 변환하지만 fallback 라벨
    "cost":                 "비용",     # _humanize 가 cost_usd → cost 변환 후 사용
    "elapsed_ms":           "소요",     # _humanize 가 "elapsed" 로 변환하지만 fallback
    "elapsed_sec":          "소요",
    "elapsed":              "소요",     # _humanize 변환 후 사용
    "size_bytes":           "크기B",
    "file":                 "파일",
    "code_id":              "코드ID",
    "tool":                 "도구",
    "message":              "메시지",
    "in_progress":          "진행중",
    "done":                 "완료",
    "total":                "전체",
    "count":                "개수",
    "batch":                "배치",
    "call_sites":           "call수",
    "max_limit":            "한도",
    # 도구 인자 라벨 (ndjson_stream._mirror_tool_event 가 박는 영어 키 자동 한글화)
    "path":                 "경로",
    "base":                 "경로",
    "pattern":              "패턴",
    "path_glob":            "글롭",
    "max_lines":            "최대",
    "anchor":               "기준행",
    "start":                "시작",
    "end":                  "끝",
    "name":                 "이름",
    "table_name":           "테이블",
    "query":                "쿼리",
    "depth":                "깊이",
    "wiped":                "초기화",
    "internal":             "내부",
    "external":             "외부",
    "table_embedded":       "테이블임베드",
    "column_embedded":      "컬럼임베드",
    "code_embedded":    "코드노드임베드",
    "builder":              "빌더",
    "big_code_nodes":        "큰코드노드",
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


def _level_from_name(name: str) -> int:
    """검증된 표준 로그 레벨 이름을 정수로 바꾼다."""
    normalized = name.strip().upper()
    levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    try:
        return levels[normalized]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 로그 레벨입니다: {name!r}") from exc


def _configure_root_once() -> None:
    """root logger 1회 설정 (idempotent).

    레벨은 ``SETTINGS.observability.log_level`` 사용 (``shared.config.settings`` 단일 진입점).
    이미 핸들러 있으면 건너뜀 (uvicorn 기본 핸들러 보존).

    외부 라이브러리 노이즈 차단:
    - ``neo4j.notifications`` — 매 run 마다 ``Schema.IndexOrConstraintAlreadyExists`` 12 줄 도배.
    - ``httpx`` — 매 LLM 호출마다 ``HTTP Request: POST ... 200 OK`` (중복).
    - ``httpcore`` — httpx 보조.
    → WARNING 이상만 root 로 통과 (실제 에러는 그대로 보임).
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_KoreanLabelFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
    root.setLevel(_level_from_name(SETTINGS.observability.log_level))

    # 외부 라이브러리 노이즈 차단 — INFO 줄 안 흘러옴, 실제 에러만 통과.
    for noisy in ("neo4j.notifications", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_root_once()


# ── run-scope 파일 핸들러 (analyzer.log + error.log) ─────────

def attach_run_file_handlers(run_dir: Path) -> list[logging.FileHandler]:
    """``<run_dir>/analyzer.log`` + ``<run_dir>/error.log`` 핸들러 attach.

    ``SETTINGS.observability.file_logging_enabled=False`` 이면 attach 안 함 (성능 영향 0 / 디스크 I/O 0) —
    빈 리스트 반환. 호출자는 결과 length 0 이면 detach 시 자동 no-op.

    ``delay=True`` 로 핸들러 생성 — 첫 ``emit()`` 시점에 파일 open. 빈 run 은 빈 파일 X.

    Args:
        run_dir: ``run.run_dir`` — 이미 ``mkdir`` 된 디렉토리. 호출자가 책임.

    Returns:
        attach 한 핸들러 리스트 — ``detach_run_file_handlers`` 의 입력.
    """
    if not SETTINGS.observability.file_logging_enabled:
        return []

    root = logging.getLogger()
    root_level = _level_from_name(SETTINGS.observability.log_level)

    # 파일 핸들러도 한글 Formatter 사용 — analyzer.log / error.log 의 가독성 통일.
    file_formatter = _KoreanLabelFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    handlers: list[logging.FileHandler] = []
    try:
        # analyzer.log — root 레벨 (INFO 기본) 이상 전체
        main_handler = logging.FileHandler(
            run_dir / _ANALYZER_LOG_FILENAME, mode="a", encoding="utf-8", delay=True,
        )
        handlers.append(main_handler)
        main_handler.setLevel(root_level)
        main_handler.setFormatter(file_formatter)
        root.addHandler(main_handler)

        # error.log — WARNING 이상만 (사고 추적용)
        error_handler = logging.FileHandler(
            run_dir / _ERROR_LOG_FILENAME, mode="a", encoding="utf-8", delay=True,
        )
        handlers.append(error_handler)
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(file_formatter)
        root.addHandler(error_handler)
    except BaseException:
        detach_run_file_handlers(handlers)
        raise

    return handlers


def detach_run_file_handlers(handlers: list[logging.FileHandler]) -> None:
    """``attach_run_file_handlers`` 가 반환한 핸들러들 remove + close.

    ``finally`` 블록에서 호출 — close 안 하면 Windows 에서 stale fd가 다음 run의
    선택된 출력 루트 초기화를 권한 오류로 실패시킨다.

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
            except Exception as error:  # noqa: BLE001 — 다음 핸들러 정리는 계속
                logging.getLogger(__name__).warning(
                    "로그 파일 핸들러 close 실패: %s: %s",
                    type(error).__name__,
                    error,
                )


def get_logger(component: str) -> logging.Logger:
    """모듈별 로거 (component = 모듈 경로)."""
    return logging.getLogger(component)


def _format_kv(kwargs: dict[str, Any]) -> str:
    """key=value 한글 라벨로 친절 직렬화 + 자동 단위 변환 + 0/None/빈문자 제거.

    형식: ``라벨: value · 라벨: value`` (가운데 점 구분, 콜론 + 공백).
    ``_KEY_LABELS`` 매핑에 없는 키는 영어 그대로 (graceful fallback).
    ``_humanize`` 가 elapsed / cost / cached / model 의 단위/형식 자동 변환.
    """
    if not kwargs:
        return ""
    kwargs = _humanize(dict(kwargs))  # mutating copy
    parts = []
    for k, v in kwargs.items():
        if v is None or v == 0 or v == "":
            continue
        label = _KEY_LABELS.get(k, k)
        parts.append(f"{label}: {v}")
    return " │ " + " · ".join(parts) if parts else ""


def _humanize(kw: dict[str, Any]) -> dict[str, Any]:
    """단위 / 형식 자동 변환 — 호출처는 raw 값만 박고 이 함수가 친절히 가공.

    - ``elapsed_ms`` ≥ 1000 → ``elapsed`` 키로 "7.5초", 미만은 "16ms"
    - ``elapsed_sec`` → ``elapsed`` "0.1초"
    - ``cost_usd`` → ``cost`` "$0.000" (소수 3자리)
    - ``cached_tokens`` + ``input_tokens`` → ``cached_tokens`` "18,560 (98%)"
    - ``model`` 의 ``-2026-03-17`` 같은 날짜 suffix 자동 제거
    - 큰 정수 (≥ 10000) → 쉼표 표기 ("18,909")
    """
    # elapsed_ms → 초 또는 ms
    if "elapsed_ms" in kw:
        try:
            ms = float(kw["elapsed_ms"])
            kw.pop("elapsed_ms")
            kw["elapsed"] = f"{ms/1000:.1f}초" if ms >= 1000 else f"{int(ms)}ms"
        except (TypeError, ValueError):
            kw["elapsed"] = str(kw.pop("elapsed_ms"))
    # elapsed_sec → 초
    if "elapsed_sec" in kw:
        elapsed_sec = kw.pop("elapsed_sec")
        try:
            sec = float(elapsed_sec)
            kw["elapsed"] = f"{sec:.1f}초"
        except (TypeError, ValueError):
            kw["elapsed"] = str(elapsed_sec)
    # cost_usd → $0.000
    if "cost_usd" in kw:
        v = kw.pop("cost_usd")
        try:
            kw["cost"] = f"${float(v):.3f}"
        except (TypeError, ValueError):
            kw["cost"] = f"${v}"
    # model 의 날짜 suffix 제거
    if "model" in kw and isinstance(kw["model"], str):
        import re
        kw["model"] = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", kw["model"])
    # cached_tokens + input_tokens → "18,560 (98%)"
    cached = kw.get("cached_tokens")
    inp = kw.get("input_tokens")
    if cached and inp:
        try:
            pct = int(100 * float(cached) / float(inp))
            kw["cached_tokens"] = f"{int(cached):,} ({pct}%)"
        except (TypeError, ValueError, ZeroDivisionError):
            kw["cached_tokens"] = str(cached)
    # 큰 정수 쉼표 표기 (단 토큰 / 매치 / 함수수 같은 분석 카운터)
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        v = kw.get(k)
        if isinstance(v, int) and v >= 10000:
            kw[k] = f"{v:,}"
    return kw


def _label_operation(operation: str) -> str:
    """operation 이름 → 한글 라벨 (매핑 없으면 원래 이름)."""
    return _OPERATION_LABELS.get(operation, operation)


def _emit_to_stream_safely(
    stream_emit: Callable[[dict], None],
    event: dict,
) -> None:
    """보조 NDJSON 전달 실패를 로컬 로그에 남기고 원래 결과는 보존한다."""
    try:
        stream_emit(event)
    except Exception as error:  # noqa: BLE001 — 원래 오류/경고를 2차 진단 실패로 대체하지 않음
        get_logger("shared.events.runtime_logger").warning(
            "NDJSON 로그 mirror 실패: %s: %s",
            type(error).__name__,
            error,
        )


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
        component: 모듈 경로 (예: ``"pipeline.steps.step7_analyze_code"``).
        operation: 함수/작업 이름 (예: ``"analyze_code_three_split"``).
        error: Exception 또는 메시지 문자열.
        stream_emit: NDJSON 스트림 emit 함수 (없으면 file handler / stderr 만).
        **context: 추가 컨텍스트 (run_id / code_id 등).
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
        _emit_to_stream_safely(stream_emit, {
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
        _emit_to_stream_safely(stream_emit, {
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
        [INFO] step5.link_calls_by_name: done call_sites=1158 internal=773 external=312
        [INFO] llm.call: done in=4500 out=850 cached=4400 cost=$0.0014 elapsed=8.2s

    Args:
        component: 모듈 / step 식별자 (예: ``"step1.parse_ddl"`` / ``"llm.call"``).
        operation: 진행 이벤트 이름 (``"start"`` / ``"done"`` / ``"batch"`` / ``"round"`` 등).
        message: 추가 설명 (선택, 기본 빈 문자열 — 통계만 충분할 때 생략).
        **context: 통계 / 컨텍스트 (개수 / cost / elapsed_sec 등).
    """
    kv = _format_kv(context)
    op_label = _label_operation(operation)
    # body 가 ` │ ` (kv 구분자) 로 시작하면 op_label 과 자연 결합 (콜론 X — 중복 차단).
    # message 있을 땐 옛 형식 유지 (op_label: message [kv]).
    if message:
        line = f"{op_label}: {message}{kv}"
    elif kv:
        line = f"{op_label}{kv}"
    else:
        line = op_label
    get_logger(component).info(line)
