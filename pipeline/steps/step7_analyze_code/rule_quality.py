"""결정론 rules/examples 품질 측정기 — 산출물은 수정하지 않는다.

최초 분석 결과에 반복·잘림 신호가 있는지 관측해 로그와 품질 플래그로 남긴다. 결과를
다른 LLM에 보내 재작성·병합하거나 자동 중복 제거하지 않는다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from pipeline.models.code_analysis_output import ExampleItem, RulesExamplesOutput

# ── 임계 시작값 (research.md §1) — 실측 후 조정. 이 파일이 단일 진실 ──
DUP_RATIO_ANOMALY = 0.20       # 정규화 후 완전중복 Example 비율
REPEAT_GROUP_ANOMALY = 3       # 같은 (given,when,then,tables) 반복 횟수
EXAMPLE_CAP = 24               # code_analysis_output.RuleItem.examples max_length (도달=잘림 의심)

_NON_WORD = re.compile(r"[^\w가-힣]", re.UNICODE)


@dataclass
class RuleQualityReport:
    """결정론 품질검사 결과 — 로그와 품질 플래그용."""
    is_anomaly: bool
    total_examples: int
    unique_examples: int
    dup_ratio: float
    max_group: int
    cap_reached: bool
    duplicate_rule_statements: int
    reasons: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    """정규화: 소문자 + 공백·구두점 제거 (문장 변주를 같은 키로)."""
    return _NON_WORD.sub("", (text or "").lower())


def _example_key(ex: ExampleItem) -> tuple:
    """Example 의미 키 = (given, when, then, 정렬된 affected_tables)."""
    tables = tuple(sorted((_norm(t.table), t.op) for t in ex.affected_tables))
    return (_norm(ex.given), _norm(ex.when), _norm(ex.then), tables)


def check_rule_quality(rules_out: RulesExamplesOutput, code_body: str = "") -> RuleQualityReport:
    """최초 분석 결과 → 이상 여부. 이상 신호가 하나라도 있으면 ``is_anomaly=True``."""
    keys = [_example_key(ex) for r in rules_out.rules for ex in r.examples]
    total = len(keys)

    # Rule statement 중복 (load_target_orders: "Rule 2개가 사실상 동일")
    rule_stmts = [_norm(r.statement) for r in rules_out.rules if r.statement]
    stmt_counts = Counter(rule_stmts)
    dup_rule_stmts = sum(c - 1 for c in stmt_counts.values() if c > 1)

    if total == 0:
        return RuleQualityReport(
            is_anomaly=bool(dup_rule_stmts),
            total_examples=0, unique_examples=0, dup_ratio=0.0, max_group=0,
            cap_reached=False, duplicate_rule_statements=dup_rule_stmts,
            reasons=(["Rule statement 중복"] if dup_rule_stmts else []),
        )

    counts = Counter(keys)
    unique = len(counts)
    dup_ratio = 1.0 - unique / total
    max_group = max(counts.values())
    cap_reached = any(len(r.examples) >= EXAMPLE_CAP for r in rules_out.rules)

    reasons: list[str] = []
    if dup_ratio >= DUP_RATIO_ANOMALY:
        reasons.append(f"정규화 중복 {dup_ratio:.0%}")
    if max_group >= REPEAT_GROUP_ANOMALY:
        reasons.append(f"동일 의미 {max_group}회 반복")
    if cap_reached:
        reasons.append(f"examples 캡({EXAMPLE_CAP}) 도달 — 잘림 의심")
    if dup_rule_stmts:
        reasons.append(f"Rule statement 중복 {dup_rule_stmts}건")

    return RuleQualityReport(
        is_anomaly=bool(reasons),
        total_examples=total, unique_examples=unique, dup_ratio=dup_ratio,
        max_group=max_group, cap_reached=cap_reached,
        duplicate_rule_statements=dup_rule_stmts, reasons=reasons,
    )
