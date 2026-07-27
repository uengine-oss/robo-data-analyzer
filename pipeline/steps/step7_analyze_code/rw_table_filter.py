"""spec 027 — 테이블 귀속을 함수의 결정론 reads/writes 로 제한.

LLM(code_table_insights·code_rules_examples)이 귀속한 테이블이 함수의 결정론
``reads ∪ writes`` 집합 밖이면 드롭한다. 대표 오류: 디스패처 함수가 callee DBIO/호출
함수명(예: ``e800_dbio_rltm_auth_hst_u0004``)을 테이블로 착각해 가짜 AFFECTS_TABLE /
table_insights 를 만드는 것. reads/writes 가 비면(직접 DB 안 건드림) 모든 테이블 귀속을
드롭한다. 드롭은 조용히 넘기지 않고 표면화한다(Constitution IV) — 단 심각도는 사실에
맞춘다(FR-005): 디스패처(allowed 빔)의 예상 드롭은 per-함수 INFO + run 종료 시 집계 1줄
(``flush_drop_summary``), 진짜 오귀속(allowed 있는데 밖 테이블)은 발생 즉시 WARNING.

분류/매칭 알고리즘·스키마 불변 — LLM 출력의 노이즈만 제거(결정론 그라운딩).
"""
from __future__ import annotations

from typing import Iterable, Optional

from shared.events.runtime_logger import log_info, log_warning

_STEP = "step7.analyze_code.rw_filter"

# run 단위 디스패처 드롭 집계 — step7이 reset/flush (analyzer = 단일 run).
# 디스패처 예상 드롭을 per-함수 WARNING 으로 쌓으면 error.log 소음 → per-함수는 INFO
# (analyzer.log 추적용)로만, run 종료 시 여기 집계를 1줄로 표면화한다(FR-005·FR-005a).
_drop_stats = {"funcs": 0, "count": 0}


def reset_drop_stats() -> None:
    """step7 시작 시 — 디스패처 드롭 집계 초기화."""
    _drop_stats["funcs"] = 0
    _drop_stats["count"] = 0


def flush_drop_summary() -> None:
    """step7 종료 시 — 디스패처 예상 드롭을 error.log 1줄로 표면화 후 초기화.

    per-함수 상세는 analyzer.log INFO 에 이미 있음(노이즈 0). 진짜 오귀속(allowed 비지
    않은 드롭)은 발생 즉시 per-함수 WARNING 으로 따로 표면화됨 — 여기 집계 대상 아님.
    """
    if _drop_stats["count"]:
        log_warning(
            _STEP, "table_attr_off_rw_dispatcher",
            f"직접 R/W 없는 디스패처의 callee 테이블 귀속 드롭 "
            f"{_drop_stats['funcs']}함수 {_drop_stats['count']}건 (예상 동작 · 상세 analyzer.log)",
            dispatcher_funcs=_drop_stats["funcs"], dropped=_drop_stats["count"],
        )
    reset_drop_stats()


def constrain_tables_to_rw(
    code_id: str,
    reads: Iterable[str],
    writes: Iterable[str],
    insights_out: Optional[object] = None,   # TableInsightsOutput | None
    rules_out: Optional[object] = None,       # RulesExamplesOutput | None
    allowed_columns_by_table: Optional[dict[str, Iterable[str]]] = None,
    table_op: Optional[dict[str, str]] = None,
) -> int:
    """LLM 귀속 테이블을 reads∪writes 정확 ID로 제한 — in-place 수정, 드롭 수 반환.

    - ``insights_out.table_insights[*].table``
    - ``rules_out.rules[*].examples[*].affected_tables[*].table``
    중 허용 집합 밖은 제거. reads/writes 비면 허용 집합도 비어 전부 드롭(디스패처).

    ``table_op`` (spec 100): {table_id: 권위 op(READ/INSERT/UPDATE/DELETE/MERGE)}. 한
    (함수,테이블)이 **단일 결정론 op** 일 때만 채워진다(모호하면 제외). LLM 이 낡은 주석에
    속아 op 를 뒤바꾼 경우(예: SQL_KEY _u=UPDATE 인데 INSERT 로 서술) 이 권위값으로 교정한다.
    테이블 제한(spec 027)과 같은 원리 — 의미 판단은 결정론이 소유하고 LLM 창작을 덮지 않는다.
    """
    read_exact = {str(t) for t in (reads or ())}
    write_exact = {str(t) for t in (writes or ())}
    allowed_exact = read_exact | write_exact

    def resolve(table: object) -> str | None:
        exact = str(table)
        return exact if exact in allowed_exact else None
    dropped = 0
    role_dropped = 0
    op_corrected = 0
    off: set[str] = set()
    table_op = table_op or {}

    kept_table_ids: set[str] = set()
    if insights_out is not None and getattr(insights_out, "table_insights", None):
        kept = []
        for ti in insights_out.table_insights:
            canonical = resolve(ti.table)
            role = getattr(ti, "role", None)
            if canonical and (
                role is None
                or _role_matches_direct_access(
                    role,
                    is_read=canonical in read_exact,
                    is_write=canonical in write_exact,
                    authoritative_op=table_op.get(canonical),
                )
            ):
                ti.table = canonical
                kept_table_ids.add(canonical)
                _constrain_columns(
                    code_id, ti, canonical, allowed_columns_by_table,
                )
                kept.append(ti)
            else:
                dropped += 1
                if canonical:
                    role_dropped += 1
                    log_warning(
                        _STEP,
                        "table_role_off_rw",
                        "LLM table role이 직접 R/W 근거와 모순되어 insight 전체 기각",
                        code_id=code_id,
                        table=canonical,
                        role=role,
                        reads=canonical in read_exact,
                        writes=canonical in write_exact,
                        authoritative_op=table_op.get(canonical),
                    )
                else:
                    off.add(str(ti.table))
        insights_out.table_insights = kept

    if insights_out is not None and allowed_exact:
        missing = sorted(allowed_exact - kept_table_ids)
        if missing:
            log_warning(
                _STEP, "table_insight_missing",
                f"LLM table_insights 누락 {len(missing)}건: {missing[:8]}",
                code_id=code_id, missing=missing[:8],
            )

    if rules_out is not None and getattr(rules_out, "rules", None):
        for rule in rules_out.rules:
            for ex in getattr(rule, "examples", None) or []:
                ats = getattr(ex, "affected_tables", None)
                if not ats:
                    continue
                kept = []
                for at in ats:
                    canonical = resolve(at.table)
                    if canonical:
                        at.table = canonical
                        # 권위 op 교정 — 단일 결정론 op 가 있고 LLM op 가 다르면 덮는다.
                        auth = table_op.get(canonical)
                        if auth and at.op != auth:
                            at.op = auth
                            op_corrected += 1
                        kept.append(at)
                    else:
                        dropped += 1
                        off.add(str(at.table))
                ex.affected_tables = kept

    if op_corrected:
        # LLM op 오귀속을 권위 op 로 덮은 수 — 표면화(헌법 IV). 잦으면 프롬프트/입력 점검 신호.
        log_info(_STEP, "table_op_corrected",
                 f"권위 op(SQL_KEY 유래)로 affected_tables op 교정 {op_corrected}건",
                 code_id=code_id, corrected=op_corrected)
    if off:
        off_dropped = dropped - role_dropped
        # 표면화(Constitution IV) — 단 심각도는 사실에 맞춘다(FR-005).
        if not allowed_exact:
            # 직접 R/W 없는 디스패처: callee 테이블이 컨텍스트에 노출돼 생기는 예상된 드롭.
            # per-함수는 INFO(analyzer.log 추적용), run 집계는 flush 가 1줄로 표면화(노이즈 0).
            _drop_stats["funcs"] += 1
            _drop_stats["count"] += off_dropped
            log_info(
                _STEP, "table_attr_off_rw",
                f"디스패처(직접 R/W 없음) callee 테이블 귀속 {off_dropped}건 드롭: {sorted(off)[:8]}",
                code_id=code_id, dropped=off_dropped,
            )
        else:
            # reads/writes 있는데 밖 테이블 = 진짜 오귀속 → 발생 즉시 WARNING(error.log).
            log_warning(
                _STEP, "table_attr_off_rw",
                f"reads/writes 밖 테이블 오귀속 {off_dropped}건 드롭: {sorted(off)[:8]}",
                code_id=code_id, dropped=off_dropped, allowed=sorted(allowed_exact)[:8],
            )
    return dropped


def _role_matches_direct_access(
    role: str,
    *,
    is_read: bool,
    is_write: bool,
    authoritative_op: str | None,
) -> bool:
    """폐쇄형 role이 결정론 R/W 방향과 모순되지 않는지만 검증한다.

    ``lookup`` 대 ``READ-only`` 및 ``append-only`` 대 ``WRITE-only``처럼 업무 맥락이
    필요한 하위 의미는 LLM이 소유한다. 이 함수는 그 값을 고쳐 쓰지 않고, 읽기 전용
    근거에 쓰기 역할을 붙이는 식의 명백한 방향 모순만 기각한다. ``upsert``는 단일
    결정론 MERGE 근거가 있을 때만 인정한다.
    """
    if role in {"READ-only", "lookup"}:
        return is_read and not is_write
    if role in {"WRITE-only", "append-only"}:
        return is_write and not is_read
    if role == "WRITE-then-READ":
        return is_read and is_write
    if role == "upsert":
        return is_write and authoritative_op == "MERGE"
    return False


def _constrain_columns(
    code_id: str,
    table_insight: object,
    table_id: str,
    allowed_columns_by_table: Optional[dict[str, Iterable[str]]],
) -> None:
    """컬럼 출력을 해당 DDL 테이블의 실제 이름과 정확히 일치할 때만 유지한다."""
    if allowed_columns_by_table is None:
        return
    allowed = {
        str(name): str(name)
        for name in allowed_columns_by_table.get(table_id, ())
        if str(name).strip()
    }
    columns = list(getattr(table_insight, "columns", None) or [])
    kept = []
    rejected: list[str] = []
    for column in columns:
        raw = str(getattr(column, "name", ""))
        canonical = allowed.get(raw)
        if canonical:
            column.name = canonical
            kept.append(column)
        else:
            rejected.append(raw)
    table_insight.columns = kept
    if rejected:
        log_warning(
            _STEP, "column_off_ddl",
            f"DDL allowlist 밖 컬럼 {len(rejected)}건 드롭: {rejected[:8]}",
            code_id=code_id, table=table_id, rejected=rejected[:8],
        )
