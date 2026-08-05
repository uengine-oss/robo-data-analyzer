"""wrapper 규칙 DSL → ``call_regex`` 컴파일러 (spec 108).

에이전트는 빈칸(규칙 필드)만 채우고, 정규식은 여기서만 기계 생성한다.
- 산출 정규식은 ``<entity>``·``<op_token>`` named group 을 **구조상 항상** 포함
  — entity 누락 죽은 규칙(jpa/ejb/mybatis 스킬 사고) 재발 불가.
- 형태 오류(빈 필드·잘못된 문자셋)는 즉시 raise — 조용한 통과 금지 (헌법 IV).
- 컴파일 결과는 기존 ``TableAccessPattern`` 으로 합류 → 검증(verify_table_patterns)·
  링킹 관문은 종전 그대로 하나다.
"""
from __future__ import annotations

import re

from agents.contracts import (
    EmbeddedSqlRule,
    MethodCallRule,
    SqlFragmentRule,
    TableAccessExclusionPattern,
    TableAccessExclusionRule,
    TableAccessPattern,
    TableAccessRule,
    WrapperKeyRule,
    WrapperKeyExclusionRule,
)

# C 문자열 개행 이음 허용 gap — ``"INSERT INTO "\n"TBL"`` (shopmall 형태 승계)
_STRING_GAP = r'(?:\s|"\s*)+'
_IDENT_BODY = r"[A-Za-z_][A-Za-z0-9_]*"


class RuleCompileError(ValueError):
    """규칙 형태 오류 — 에이전트/스킬 산출물이 계약을 벗어남."""


_RULE_KINDS = {
    "wrapper_key": WrapperKeyRule,
    "method_call": MethodCallRule,
    "sql_fragment": SqlFragmentRule,
    "embedded_sql": EmbeddedSqlRule,
}
_EXCLUSION_RULE_KINDS = {
    "wrapper_key": WrapperKeyExclusionRule,
}


def parse_rule(raw: dict) -> TableAccessRule:
    """YAML/Agent 규칙 dict를 폐쇄된 DSL 모델로 변환한다."""
    kind = (raw or {}).get("kind")
    model = _RULE_KINDS.get(kind)
    if model is None:
        raise RuleCompileError(f"알 수 없는 table_access_rules.kind: {kind!r}")
    return model(**raw)


def parse_exclusion_rule(raw: dict) -> TableAccessExclusionRule:
    """Parse a closed non-TABLE source-shape rule from SKILL data."""
    kind = (raw or {}).get("kind")
    model = _EXCLUSION_RULE_KINDS.get(kind)
    if model is None:
        raise RuleCompileError(
            f"알 수 없는 table_access_exclusion_rules.kind: {kind!r}"
        )
    return model(**raw)


def split_rules(
    rules: list[TableAccessRule],
) -> tuple[list[TableAccessRule], list[EmbeddedSqlRule]]:
    """규칙을 (정규식 컴파일 대상, 임베디드 SQL) 로 분리 (spec 115).

    embedded_sql 은 정규식 하나로 표현되지 않는다 — 블록 추출 후 DML 의미표가
    직접 판정하는 별도 경로 (``RegexTableLinker.link(embedded_rules=…)``).
    """
    embedded = [r for r in rules if isinstance(r, EmbeddedSqlRule)]
    compilable = [r for r in rules if not isinstance(r, EmbeddedSqlRule)]
    return compilable, embedded


def compile_rule(rule: TableAccessRule) -> TableAccessPattern:
    """규칙 1건 → ``TableAccessPattern``. 산출 정규식은 named group 2종 보장."""
    if isinstance(rule, WrapperKeyRule):
        regex = _compile_wrapper_key(rule)
    elif isinstance(rule, MethodCallRule):
        regex = _compile_method_call(rule)
    elif isinstance(rule, SqlFragmentRule):
        regex = _compile_sql_fragment(rule)
    elif isinstance(rule, EmbeddedSqlRule):
        raise RuleCompileError(
            f"{rule.id}: embedded_sql 은 패턴 컴파일 대상이 아님 — split_rules() 로 분리할 것"
        )
    else:                                    # 방어 — 새 kind 는 별도 spec 으로만 (D1)
        raise RuleCompileError(f"알 수 없는 규칙 kind: {type(rule).__name__}")

    try:
        compiled = re.compile(regex)
    except re.error as e:                    # 골격 결함 — 사용자 데이터가 아니라 코드 버그
        raise RuleCompileError(f"{rule.id}: 컴파일 산출 정규식 불량 — {e}") from e
    if "entity" not in compiled.groupindex or "op_token" not in compiled.groupindex:
        raise RuleCompileError(f"{rule.id}: named group 누락 — 컴파일러 결함")
    return TableAccessPattern(id=rule.id, call_regex=regex, op_mappings=rule.op_mappings)


def compile_rules(rules: list[TableAccessRule]) -> list[TableAccessPattern]:
    return [compile_rule(rule) for rule in rules]


def compile_exclusion_rule(
    rule: TableAccessExclusionRule,
) -> TableAccessExclusionPattern:
    """Compile one non-TABLE source shape without giving it edge semantics."""
    if not isinstance(rule, WrapperKeyExclusionRule):
        raise RuleCompileError(f"알 수 없는 exclusion 규칙 kind: {type(rule).__name__}")
    _require(rule.id, bool(rule.wrapper.strip()), "wrapper 함수명이 비었음")
    _require(rule.id, bool(rule.entity_suffix), "entity_suffix가 비었음")
    _require(rule.id, bool(rule.separator), "separator가 비었음")
    ops = _token_alternation(rule.id, rule.op_tokens)
    op_group = f"(?P<op_token>{ops})" if ops.startswith("[") else f"(?P<op_token>(?:{ops}))"
    digits = rf"\d{{{rule.suffix_digits}}}" if rule.suffix_digits else ""
    regex = (
        rf'{re.escape(rule.wrapper.strip())}\s*\(\s*"'
        rf"(?P<entity>[A-Za-z0-9_]+?){re.escape(rule.entity_suffix)}"
        rf"{re.escape(rule.separator)}{op_group}{digits}\""
    )
    try:
        compiled = re.compile(regex)
    except re.error as error:
        raise RuleCompileError(f"{rule.id}: exclusion 정규식 불량 — {error}") from error
    if {"entity", "op_token"} - set(compiled.groupindex):
        raise RuleCompileError(f"{rule.id}: exclusion named group 누락 — 컴파일러 결함")
    return TableAccessExclusionPattern(id=rule.id, call_regex=regex)


def compile_exclusion_rules(
    rules: list[TableAccessExclusionRule],
) -> list[TableAccessExclusionPattern]:
    return [compile_exclusion_rule(rule) for rule in rules]


# ── 템플릿별 골격 ────────────────────────────────────────────


def _require(rule_id: str, condition: bool, message: str) -> None:
    if not condition:
        raise RuleCompileError(f"{rule_id}: {message}")


def _token_alternation(rule_id: str, tokens: list[str]) -> str:
    _require(rule_id, bool(tokens) and all(t.strip() for t in tokens), "op 토큰이 비었음")
    ordered = sorted({t.strip() for t in tokens}, key=len, reverse=True)  # 긴 토큰 우선
    if all(len(t) == 1 for t in ordered):
        return f"[{''.join(re.escape(t) for t in sorted(ordered))}]"
    return "|".join(re.escape(t) for t in ordered)


def _compile_wrapper_key(rule: WrapperKeyRule) -> str:
    _require(rule.id, bool(rule.wrapper.strip()), "wrapper 함수명이 비었음")
    _require(rule.id, bool(rule.separator), "separator 가 비었음")
    ops = _token_alternation(rule.id, rule.op_tokens)
    op_group = f"(?P<op_token>{ops})" if ops.startswith("[") else f"(?P<op_token>(?:{ops}))"
    exclusion = f"(?<!{re.escape(rule.entity_not_ending)})" if rule.entity_not_ending else ""
    digits = rf"\d{{{rule.suffix_digits}}}" if rule.suffix_digits else ""
    return (
        rf'{re.escape(rule.wrapper.strip())}\s*\(\s*"'
        rf"(?P<entity>[A-Za-z0-9_]+?){exclusion}{re.escape(rule.separator)}"
        rf'{op_group}{digits}"'
    )


def _compile_method_call(rule: MethodCallRule) -> str:
    prefixes = "|".join(
        re.escape(p.strip()) for p in sorted({p for p in rule.op_prefixes if p.strip()},
                                             key=len, reverse=True)
    )
    _require(rule.id, bool(prefixes), "op_prefixes 가 비었음")
    if rule.entity_from == "receiver":
        _require(rule.id, bool(rule.receiver_suffix.strip()),
                 "entity_from=receiver 는 receiver_suffix 필수")
        return (
            rf"(?P<entity>\w+){re.escape(rule.receiver_suffix.strip())}"
            rf"\.(?P<op_token>{prefixes})\w*\s*\("
        )
    _require(rule.id, bool(rule.receiver_name.strip()),
             "entity_from=first_arg_string 은 receiver_name 필수")
    charset = rule.first_arg_charset.strip() or r"\w"
    body = charset if charset.startswith("[") else f"[{charset}]"
    return (
        rf"{re.escape(rule.receiver_name.strip())}"
        rf'\.(?P<op_token>{prefixes})\s*\(\s*"(?P<entity>{body}+)"'
    )


def _compile_sql_fragment(rule: SqlFragmentRule) -> str:
    _require(rule.id, bool(rule.keyword.strip()), "keyword 가 비었음")
    parts = [rf"(?P<op_token>{re.escape(rule.keyword.strip())})"]
    if rule.before_entity.strip():
        parts.append(re.escape(rule.before_entity.strip()))
    regex = _STRING_GAP.join(parts) + _STRING_GAP + rf"(?P<entity>{_IDENT_BODY})"
    if rule.after_entity.strip():
        regex += _STRING_GAP + re.escape(rule.after_entity.strip())
    return regex
