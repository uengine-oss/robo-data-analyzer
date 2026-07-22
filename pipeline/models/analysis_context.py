"""분석 컨텍스트 — Framework / DBMS 양쪽이 공유.

``build_context_step`` 이 생성, ``analyze_code_step`` 이 채움.

설계 원칙
─────────
- 공통 필드 = strategy 무관 (id, signature, reads/writes, callers/callees, summary).
- strategy 별 확장은 ``extra: dict[str, Any]`` 로 — dataclass 계승 X.
- 큰 코드노드 본문은 ``large_code_one_liner`` 에서 압축 후 ``one_liner`` 필드에 박음.
  → callee 본문 통째 노출 차단 ($400 사고 원인 차단).

**Framework vs DBMS 컨텍스트 구성 차이** — 같은 dataclass 가 양쪽을 흡수:
- Framework: ``callees`` (호출 그래프) — Bottom-up DAG 로 callee summary 라이브 조회.
- DBMS: effective parent/child tree metadata is assembled in step 6.

사용 흐름
─────────
1. ``build_context_step`` 이 결정적 메타 (id/signature/reads/writes/callers/callees) 로
   컨텍스트 생성. ``summary`` = None 으로 둠.
2. ``analyze_code_step`` (Bottom-up DAG, leaf → root) 이 ``summary`` 채움.
3. caller 분석 시점에 callee 컨텍스트 (``summary`` 포함) 가 입력으로 들어감 →
   본문 통째 노출 X. 토큰 폭증 차단.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ReferencedSymbolContext:
    """A referenced symbol with explicit value provenance and mutability."""

    id: str
    name: str
    kind: str
    data_type: str = ""
    declared_value: str = ""
    value_semantics: str = ""
    initialization_sources: tuple[str, ...] = ()

    def as_prompt_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "data_type": self.data_type,
            "declared_value": self.declared_value,
            "value_semantics": self.value_semantics,
            "initialization_sources": list(self.initialization_sources),
        }


@dataclass(frozen=True)
class ExternalEvidenceContext:
    """One REFER_TO-linked external chunk, included whole or not at all (spec 078)."""

    id: str
    file_path: str
    matched_key: str
    semantic_kind: str
    provenance: str
    text: str
    start_line: int
    end_line: int
    content_hash: str

    def as_prompt_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "matched_key": self.matched_key,
            "semantic_kind": self.semantic_kind,
            "provenance": self.provenance,
            "text": self.text,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content_hash": self.content_hash,
        }


@dataclass
class ControlSubtreeNode:
    """Routine-relative control tree consumed by deterministic rule splitting."""

    node_id: str
    ntype: str
    start: int
    end: int
    children: list["ControlSubtreeNode"] = field(default_factory=list)


def dump_referenced_symbols(symbols: tuple[ReferencedSymbolContext, ...]) -> str:
    """Serialize the single prompt-input contract without delimiter parsing."""
    return json.dumps(
        [symbol.as_prompt_dict() for symbol in symbols],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def dump_external_evidence(evidence: tuple[ExternalEvidenceContext, ...]) -> str:
    """Serialize linked external facts without delimiter parsing or inferred labels."""
    return json.dumps(
        [item.as_prompt_dict() for item in evidence],
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass
class AnalysisContext:
    """코드노드 1개의 누적 분석 컨텍스트 (Framework / DBMS 공유).

    공통 필드는 모든 strategy 공유. strategy 별 추가 정보는 ``extra`` dict 에
    (DBMS 의 parent / sibling 트리 메타 등 — strategy context_builder 가 채움).
    Framework 는 base 필드만으로 충분 — ``extra`` 미사용.

    새 strategy 가 필드 추가해도 이 dataclass 무변경.
    """
    # 식별 ────────────────────────────────────────────────
    id: str
    signature: str

    # 위치 ────────────────────────────────────────────────
    file_path: str
    start_line: int
    end_line: int

    # Linking 결과 (stage1 / stage2 / stage3 가 채움) ────────
    # tuple + sorted 고정 → set/dict iteration 비결정성 차단 (캐시 친화)
    reads: tuple[str, ...] = ()              # Table fqn 들
    writes: tuple[str, ...] = ()             # Table fqn 들
    callers: tuple[str, ...] = ()            # caller id 들
    callees: tuple[str, ...] = ()            # callee id 들

    # 참조 컨텍스트 (build_context_step 가 state 엣지/노드에서 결정론 도출) ────
    referenced_symbols: tuple[ReferencedSymbolContext, ...] = ()
    external_evidence: tuple[ExternalEvidenceContext, ...] = ()

    # 큰 코드노드 사전 압축 (build_context_step 가 본문 토큰 > 임계 시 채움) ────
    one_liner: Optional[str] = None          # "결제 PG 호출 + 인증 처리" 식
    token_count: int = 0                     # 본문 토큰 수

    # 의미 분석 결과 (analyze_code_step 이 채움) ──────────────
    summary: Optional[str] = None            # 마커 없는 1~3줄 짧은 요약 (SummaryOutput 검증)

    # Bottom-up DAG 메타 (call_cycles 가 채움) ──────────────
    is_in_cycle: bool = False                # SCC cycle 내부 → callee summary 없이 분석

    # strategy 별 확장 (자유 dict — 새 필드 추가 시 base 무변경) ────
    extra: dict[str, Any] = field(default_factory=dict)
