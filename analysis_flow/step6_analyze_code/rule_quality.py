"""결정론 rules/examples 품질검사 (spec 054 US3 / FR-017·FR-018) — LLM 미사용.

최초 "함수 전체" 분석 결과에 **반복·잘림** 신호가 있는지 판정한다. 이상이면 호출부(폴백)가
AST 예산 분할 → 조각분석 → 병합으로 복구한다. 임계 시작값 근거는
``specs/054-.../research.md §1`` (load_target_orders 88% 중복·24회 반복 등 보존 run).

의미 중복(문장 표현이 다른 동일 의미)은 결정론 정규화만으로 완전 검출할 수 없다 —
여기서는 **정규화 완전중복·반복 그룹·캡 도달**만 신뢰 신호로 쓰고, 나머지는 병합 LLM +
재검사의 몫으로 남긴다(spec 054 Assumptions: "검증됨"과 "best-effort" 구분).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from data_models.code_analysis_output import ExampleItem, RulesExamplesOutput

# ── 임계 시작값 (research.md §1) — 실측 후 조정. 이 파일이 단일 진실 ──
DUP_RATIO_ANOMALY = 0.20       # 정규화 후 완전중복 Example 비율
REPEAT_GROUP_ANOMALY = 3       # 같은 (given,when,then,tables) 반복 횟수
EXAMPLE_CAP = 80               # code_analysis_output.RuleItem.examples max_length (도달=잘림 의심)

_NON_WORD = re.compile(r"[^\w가-힣]", re.UNICODE)


@dataclass
class RuleQualityReport:
    """결정론 품질검사 결과 — 폴백 트리거 판정과 로그/스트림용."""
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


def deterministic_dedupe(rules_out: RulesExamplesOutput) -> RulesExamplesOutput:
    """폴백 재검사도 실패했을 때 마지막 결정론 정리 — 완전중복 Example만 제거(FR-023).

    의미 중복은 못 지운다(정규화 한계). 지울 수 있는 완전중복만 제거해 최소 정합성 확보.
    각 Rule 안에서 정규화 키가 처음 등장한 것만 남긴다(순서 보존).
    """
    for rule in rules_out.rules:
        seen: set[tuple] = set()
        kept: list[ExampleItem] = []
        for ex in rule.examples:
            k = _example_key(ex)
            if k in seen:
                continue
            seen.add(k)
            kept.append(ex)
        rule.examples = kept
    return rules_out
