"""spec 027 — 테이블 귀속을 함수의 결정론 reads/writes 로 제한.

LLM(code_table_insights·code_rules_examples)이 귀속한 테이블이 함수의 결정론
``reads ∪ writes`` 집합 밖이면 드롭한다. 대표 오류: 디스패처 함수가 callee DBIO/호출
함수명(예: ``e800_dbio_rltm_auth_hst_u0004``)을 테이블로 착각해 가짜 AFFECTS_TABLE /
table_insights 를 만드는 것. reads/writes 가 비면(직접 DB 안 건드림) 모든 테이블 귀속을
드롭한다. 드롭은 조용히 넘기지 않고 표면화한다(Constitution IV).

분류/매칭 알고리즘·스키마 불변 — LLM 출력의 노이즈만 제거(결정론 그라운딩).
"""
from __future__ import annotations

from typing import Iterable, Optional

from shared.log.logger import log_warning

_STEP = "step6.analyze_code.rw_filter"


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
        log_warning(
            _STEP, "table_attr_off_rw",
            f"reads/writes 밖 테이블 귀속 {dropped}건 드롭 (callee 함수명 오귀속 등): "
            f"{sorted(off)[:8]}",
            code_id=code_id, dropped=dropped, allowed=sorted(allowed)[:8] or None,
        )
    return dropped
