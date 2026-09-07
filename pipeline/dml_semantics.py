"""DML 문법 의미표 로더 + 파생 — R/W 판정의 단일 진실.

표는 인접 ``dml_semantics.yaml``. 소비자:
- ``link_dbms_tables_step`` — DML 감지·write 대상 추출·dml_type
- ``regex_table_linker`` — C 리터럴 SQL 판정 (start/write/read/키워드 목격)
- ``dml_type_of`` — 구 ``graph_edges.derive_dml_type`` 의 후신 (표 밖 토큰 = "")

정규식 조립은 이 모듈의 단일 빌더만 한다 — 표에는 구문 데이터만 산다 (research D7).
로딩·형식 오류는 조용한 폴백 없이 즉시 raise (헌법 IV / FR-009).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal, Optional

import yaml

_TABLE_PATH = Path(__file__).resolve().parent / "dml_semantics.yaml"

# 대상 테이블 캡처 골격 — 스키마 수식 허용, group("entity") = 테이블명 (구 _WRITE_TARGET_RE 계약).
_ENTITY = r"(?:[\w$]+\.)?(?P<entity>[A-Za-z_][\w$]*)"

_ALLOWED_STATEMENT_FIELDS = {"relation", "dml_type", "detect", "target_after"}


class DmlSemanticsError(ValueError):
    """의미표 파일 부재·형식 오류 — 파이프라인은 명시 오류로 중단한다."""


@dataclass(frozen=True)
class StatementRule:
    """구문 1종의 의미 — 표의 한 행."""
    keyword: str                             # 키 = dml_type 기본값
    relation: Literal["READS", "WRITES"]
    dml_type: str                            # "" 허용 (WITH 현행 보존 — D3)
    detect: tuple[str, ...]                  # 감지 구문 (기본 = keyword)
    target_after: tuple[str, ...]            # WRITES 대상 직후 구문


def _phrase_re(phrase: str) -> str:
    """구문 → 정규식 조각. 공백은 임의 공백(개행 포함)으로."""
    return r"\s+".join(re.escape(word) for word in phrase.split())


def _alternation(phrases: list[str]) -> str:
    """긴 구문 우선 alternation — `DELETE FROM` 이 `DELETE` 보다 먼저 시도되게."""
    ordered = sorted(phrases, key=len, reverse=True)
    return "|".join(_phrase_re(p) for p in ordered)


class DmlSemantics:
    """표에서 파생된 판정 도구 일습. 인스턴스는 ``dml_semantics()`` lru_cache 로 공유."""

    def __init__(self, statements: dict[str, StatementRule], read_clauses: tuple[str, ...]):
        self._statements = statements
        self._read_clauses = read_clauses

        # 감지 구문 → 키워드 역인덱스 (공백 정규화 대문자 기준)
        self._detect_to_keyword: dict[str, str] = {}
        for rule in statements.values():
            for phrase in rule.detect:
                self._detect_to_keyword[" ".join(phrase.upper().split())] = rule.keyword

        all_detect = [p for r in statements.values() for p in r.detect]
        non_with_detect = [p for r in statements.values() for p in r.detect if r.keyword != "WITH"]
        write_rules = [r for r in statements.values() if r.relation == "WRITES"]

        # 구 _DML_KW_RE / _SQL_KEYWORD_RE 대체 — 본문 어디서든 첫 DML 구문.
        self.detect_regex = re.compile(rf"\b({_alternation(all_detect)})\b", re.IGNORECASE)
        # 구 _C_SQL_START_RE 대체 — 첫 토큰이 DML 인가 (WITH 접두 허용, group(1)=구문).
        self.start_regex = re.compile(
            rf"^\s*(?:WITH\b.*?\b)?({_alternation(non_with_detect)})\b",
            re.IGNORECASE | re.DOTALL,
        )
        # 구 _WRITE_TARGET_RE / _C_SQL_WRITE_TARGET_RE 대체.
        write_phrases = [p for r in write_rules for p in r.target_after]
        self.write_target_regex = re.compile(
            rf"\b(?:{_alternation(write_phrases)})\s+{_ENTITY}", re.IGNORECASE,
        )
        # 구 _C_SQL_READ_TARGET_RE 대체 (USING 추가 — D6).
        self.read_target_regex = re.compile(
            rf"\b(?:{_alternation(list(read_clauses))})\s+{_ENTITY}", re.IGNORECASE,
        )
        # write 구문별 (규칙, 정규식) — 대상별 dml_type 판정용 (US1 AS-2).
        self._write_patterns: list[tuple[StatementRule, re.Pattern]] = [
            (rule, re.compile(rf"\b(?:{_alternation(list(rule.target_after))})\s+{_ENTITY}",
                              re.IGNORECASE))
            for rule in write_rules
        ]

    # ── 판정 API ─────────────────────────────────────────────

    def first_dml(self, body: str) -> Optional[str]:
        """본문 첫 DML 구문 → 키워드 (없으면 None). 구 ``_first_dml`` 계약."""
        m = self.detect_regex.search(body)
        return self._keyword_of(m.group(1)) if m else None

    def first_statement(self, content: str) -> Optional[str]:
        """첫 토큰(WITH 접두 허용)이 DML 이면 그 키워드 — C 리터럴 outer 판정."""
        m = self.start_regex.search(content)
        return self._keyword_of(m.group(1)) if m else None

    def iter_write_targets(self, text: str) -> Iterator[tuple[re.Match, StatementRule]]:
        """write 대상 매치 전부 — (match, 그 구문의 규칙). C 경로가 span 을 쓴다."""
        for rule, pattern in self._write_patterns:
            for m in pattern.finditer(text):
                yield m, rule

    def write_target_tables(self, body: str) -> dict[str, str]:
        """대상 테이블(대문자) → dml_type. 같은 테이블 다중 구문이면 먼저 등재된 것 유지."""
        targets: dict[str, str] = {}
        for m, rule in self.iter_write_targets(body):
            targets.setdefault(m.group("entity").upper(), rule.dml_type)
        return targets

    def relation_of(self, keyword: str) -> Optional[str]:
        rule = self._statements.get(keyword.strip().upper())
        return rule.relation if rule else None

    def dml_type_of(self, op: Optional[str]) -> str:
        """토큰 → 표준 dml_type. 표 밖 토큰·빈값 = "" (구 derive_dml_type 계약 유지)."""
        if not op:
            return ""
        rule = self._statements.get(op.strip().upper())
        return rule.dml_type if rule else ""

    def _keyword_of(self, matched_phrase: str) -> str:
        normalized = " ".join(matched_phrase.upper().split())
        keyword = self._detect_to_keyword.get(normalized)
        if keyword is None:                  # 파생 regex 와 역인덱스가 어긋나면 코드 결함
            raise DmlSemanticsError(f"감지 구문 역인덱스 불일치: {matched_phrase!r}")
        return keyword


# ── 로딩 (형식 오류 즉시 raise — FR-009) ──────────────────────


def _parse_statement(keyword: str, raw: dict) -> StatementRule:
    if not isinstance(raw, dict):
        raise DmlSemanticsError(f"{keyword}: 매핑이 아님 — {type(raw).__name__}")
    unknown = set(raw) - _ALLOWED_STATEMENT_FIELDS
    if unknown:
        raise DmlSemanticsError(f"{keyword}: 알 수 없는 필드 {sorted(unknown)} — 표는 미니 언어가 아니다 (D7)")
    relation = raw.get("relation")
    if relation not in ("READS", "WRITES"):
        raise DmlSemanticsError(f"{keyword}: relation 은 READS|WRITES 여야 함 — {relation!r}")
    # dml_type: 키 없음 = 키워드 자신 / 명시 null = "" (WITH 현행 보존)
    dml_type = keyword if "dml_type" not in raw else (raw["dml_type"] or "")
    detect = tuple(raw.get("detect") or (keyword,))
    target_after = tuple(raw.get("target_after") or ())
    if relation == "WRITES" and not target_after:
        raise DmlSemanticsError(f"{keyword}: WRITES 구문은 target_after 필수")
    if any(not isinstance(p, str) or not p.strip() for p in (*detect, *target_after)):
        raise DmlSemanticsError(f"{keyword}: 빈 구문 금지")
    return StatementRule(
        keyword=keyword.upper(), relation=relation, dml_type=dml_type,
        detect=detect, target_after=target_after,
    )


@lru_cache(maxsize=None)
def dml_semantics(dialect: str = "") -> DmlSemantics:
    """의미표 로딩 + 방언 병합 → 파생 인스턴스. 오류는 즉시 raise (조용한 폴백 금지)."""
    if not _TABLE_PATH.is_file():
        raise DmlSemanticsError(f"의미표 파일 없음: {_TABLE_PATH}")
    try:
        doc = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise DmlSemanticsError(f"의미표 YAML 파싱 실패: {e}") from e
    if not isinstance(doc, dict) or "core" not in doc:
        raise DmlSemanticsError("의미표 최상위에 core 섹션 필수")

    merged_statements: dict[str, dict] = dict(doc["core"].get("statements") or {})
    read_clauses = list(doc["core"].get("read_clauses") or [])
    if dialect:
        delta = (doc.get("dialects") or {}).get(dialect) or {}
        merged_statements.update(delta.get("statements") or {})
        read_clauses.extend(delta.get("read_clauses") or [])
    if not merged_statements:
        raise DmlSemanticsError("statements 가 비어 있음")

    statements = {
        kw.upper(): _parse_statement(kw.upper(), raw)
        for kw, raw in merged_statements.items()
    }
    return DmlSemantics(statements, tuple(read_clauses))


def dml_type_of(standard_op: Optional[str]) -> str:
    """모듈 편의 함수 — 구 ``derive_dml_type`` 후신 (호출자 4곳의 단일 진실)."""
    return dml_semantics().dml_type_of(standard_op)
