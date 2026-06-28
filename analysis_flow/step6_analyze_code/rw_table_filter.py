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

from shared.log.logger import log_info, log_warning

_STEP = "step6.analyze_code.rw_filter"

# run 단위 디스패처 드롭 집계 — step6 가 reset/flush (analyzer = 단일 run).
# 디스패처 예상 드롭을 per-함수 WARNING 으로 쌓으면 error.log 소음 → per-함수는 INFO
# (analyzer.log 추적용)로만, run 종료 시 여기 집계를 1줄로 표면화한다(FR-005·FR-005a).
_drop_stats = {"funcs": 0, "count": 0}


def reset_drop_stats() -> None:
    """step6 시작 시 — 디스패처 드롭 집계 초기화."""
    _drop_stats["funcs"] = 0
    _drop_stats["count"] = 0


def flush_drop_summary() -> None:
    """step6 종료 시 — 디스패처 예상 드롭을 error.log 1줄로 표면화 후 초기화.

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


def _bare(name: object) -> str:
    """fqn/함수id 의 마지막 세그먼트 (테이블명 비교용, 수식 차이 흡수)."""
    return str(name).rsplit(".", 1)[-1]


def constrain_tables_to_rw(
    code_id: str,
    reads: Iterable[str],
    writes: Iterable[str],
    insights_out: Optional[object] = None,   # TableInsightsOutput | None
    rules_out: Optional[object] = None,       # RulesExamplesOutput | None
) -> int:
    """LLM 귀속 테이블을 reads∪writes(bare name) 로 제한 — in-place 수정, 드롭 수 반환.

    - ``insights_out.table_insights[*].table``
    - ``rules_out.examples[*].affected_tables[*].table``
    중 허용 집합 밖은 제거. reads/writes 비면 허용 집합도 비어 전부 드롭(디스패처).
    """
    allowed = {_bare(t) for t in (reads or ())} | {_bare(t) for t in (writes or ())}
    dropped = 0
    off: set[str] = set()

    if insights_out is not None and getattr(insights_out, "table_insights", None):
        kept = []
        for ti in insights_out.table_insights:
            if _bare(ti.table) in allowed:
                kept.append(ti)
            else:
                dropped += 1
                off.add(str(ti.table))
        insights_out.table_insights = kept

    if rules_out is not None and getattr(rules_out, "examples", None):
        for ex in rules_out.examples:
            ats = getattr(ex, "affected_tables", None)
            if not ats:
                continue
            kept = []
            for at in ats:
                if _bare(at.table) in allowed:
                    kept.append(at)
                else:
                    dropped += 1
                    off.add(str(at.table))
            ex.affected_tables = kept

    if dropped:
        # 표면화(Constitution IV) — 단 심각도는 사실에 맞춘다(FR-005).
        if not allowed:
            # 직접 R/W 없는 디스패처: callee 테이블이 컨텍스트에 노출돼 생기는 예상된 드롭.
            # per-함수는 INFO(analyzer.log 추적용), run 집계는 flush 가 1줄로 표면화(노이즈 0).
            _drop_stats["funcs"] += 1
            _drop_stats["count"] += dropped
            log_info(
                _STEP, "table_attr_off_rw",
                f"디스패처(직접 R/W 없음) callee 테이블 귀속 {dropped}건 드롭: {sorted(off)[:8]}",
                code_id=code_id, dropped=dropped,
            )
        else:
            # reads/writes 있는데 밖 테이블 = 진짜 오귀속 → 발생 즉시 WARNING(error.log).
            log_warning(
                _STEP, "table_attr_off_rw",
                f"reads/writes 밖 테이블 오귀속 {dropped}건 드롭: {sorted(off)[:8]}",
                code_id=code_id, dropped=dropped, allowed=sorted(allowed)[:8],
            )
    return dropped
