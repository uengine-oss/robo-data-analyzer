"""분석 컨텍스트 — Framework / DBMS 양쪽이 공유.

``build_context_step`` 이 생성, ``analyze_code_step`` 이 채움.

설계 원칙
─────────
- 공통 필드 = strategy 무관 (id, signature, reads/writes, callers/callees, 의미 필드).
- strategy 별 확장은 ``extra: dict[str, Any]`` 로 — dataclass 계승 X.
- 큰 본문은 구조적 자식 치환과 배치 예산으로 다룬다. 자유형 요약으로 원문 구조를 대체하지 않는다.

**Framework vs DBMS의 동일한 summary 수신 계약**:
- ``callees``는 이 컨텍스트가 받을 callee summary의 직접 대상이다.
- Framework에서는 routine context가 routine callee를 가진다.
- DBMS에서는 실제 prompt 수신자인 CALL context가 routine callee를 가진다.
- DBMS effective parent/child tree metadata는 별도의 구조 summary 의존으로 합쳐진다.

사용 흐름
─────────
1. ``build_context_step`` 이 결정적 메타 (id/signature/reads/writes/callers/callees) 로
   컨텍스트 생성. ``summary`` = None 으로 둠.
2. ``analyze_code_step`` (Bottom-up DAG, leaf → root) 이 의미 필드를 채움.
3. caller 분석 시점에 callee canonical ``summary``가 입력으로 들어감 →
   본문 통째 노출 X. 토큰 폭증 차단.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from shared.files.source_excerpt import number_text


SymbolKind = Literal["global", "field", "local", "parameter", "constant"]


@dataclass(frozen=True)
class ReferencedSymbolContext:
    """A referenced symbol with explicit value provenance and mutability."""

    id: str
    name: str
    kind: SymbolKind
    data_type: str = ""
    declared_value: str = ""
    value_semantics: str = ""
    initialization_sources: tuple[str, ...] = ()

    def as_prompt_dict(self) -> dict:
        return {
            "relation": "TARGET_REFERENCES_DEFINITION",
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

    def as_exact_prompt_dict(self) -> dict:
        """Return the reversible external source packet used by semantic calls."""
        return {
            "relation": "TARGET_REFER_TO_EXTERNAL_CHUNK",
            "id": self.id,
            "file_path": self.file_path,
            "matched_key": self.matched_key,
            "semantic_kind": self.semantic_kind,
            "provenance": self.provenance,
            "text": self.text,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "coordinate_system": "absolute-file-line",
            "evidence_scope": "linked-external-context",
            "content_hash": self.content_hash,
        }

    def as_prompt_dict(self) -> dict:
        return {
            "relation": "TARGET_REFER_TO_EXTERNAL_CHUNK",
            "id": self.id,
            "file_path": self.file_path,
            "matched_key": self.matched_key,
            "semantic_kind": self.semantic_kind,
            "provenance": self.provenance,
            "numbered_text": number_text(self.text, self.start_line),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "coordinate_system": "absolute-file-line",
            "evidence_scope": "linked-external-context",
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
    # parser 가 소유한 조건식 원문 (spec 016 FR-003). 빈 문자열이면 그 grammar 가
    # 조건을 두지 않는 노드(ELSE/default 등)다. analyzer 는 괄호 짝을 재파싱하지 않는다.
    expression: str = ""
    # parser grammar fact. Empty is backward-compatible pre-test; ``post`` is do-while.
    condition_timing: str = ""


@dataclass(frozen=True)
class StatementFact:
    """Routine-relative structural statement owned by the parser AST (spec 016).

    RETURN/THROW/RAISE/BREAK/CONTINUE/GOTO/LABEL/ASSIGNMENT 의 좌표와 표현 원문.
    branch_inventory 가 종료행·반환식·대입·entry 의 정본으로 소비하며, analyzer 는
    이 사실을 source 정규식으로 재탐색하지 않는다.
    """

    node_id: str
    label: str
    start: int
    end: int
    expression: str = ""
    target: str = ""
    operator: str = ""


@dataclass(frozen=True)
class CallOccurrenceFact:
    """One exact call occurrence joined to Analyzer-owned resolution.

    Parser-owned coordinates and source fragments stay unchanged.  Final binding
    fields come only from the Analyzer resolution ledger.  ``line`` is
    routine-relative so it shares the coordinate system used by ``StatementFact``
    and ``BranchInventory``.
    """

    call_id: str
    line: int
    callee: str
    call_source: str
    argument_sources: tuple[str, ...] = ()
    receiver_source: str | None = None
    source_range: tuple[int, int] | None = None
    callee_range: tuple[int, int] | None = None
    argument_ranges: tuple[tuple[int, int], ...] = ()
    receiver_range: tuple[int, int] | None = None
    scope_path: tuple[tuple[str, tuple[int, int]], ...] = ()
    grammar_rule: str = ""
    callee_source: str = ""
    callee_kind: Literal["named", "constructor", "expression"] = "named"
    context_role: Literal["condition", "effect", "non_active"] = "effect"
    condition_fact_id: str | None = None
    presence_status: Literal["active", "inactive", "conditional", "unknown"] = (
        "active"
    )
    resolution_status: str = ""
    resolution_outcome: str = ""
    resolution_scope: str | None = None
    resolution_reason: str = ""
    callee_id: str = ""
    external_identity_key: str = ""


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
    # effective R/W: DBMS 루틴은 자기 자손 statement의 R/W를 포함한다. summary와
    # RULE 허용 범위가 사용한다. direct R/W는 정확히 이 node id가 source인 그래프 엣지로,
    # table-insight처럼 현재 node의 직접 동작만 설명하는 concern이 사용한다.
    reads: tuple[str, ...] = ()              # effective Table fqn 들
    writes: tuple[str, ...] = ()             # effective Table fqn 들
    direct_reads: tuple[str, ...] = ()       # exact-source READS Table fqn 들
    direct_writes: tuple[str, ...] = ()      # exact-source WRITES Table fqn 들
    callers: tuple[str, ...] = ()            # caller id 들
    callees: tuple[str, ...] = ()            # callee id 들

    # 참조 컨텍스트 (build_context_step 가 state 엣지/노드에서 결정론 도출) ────
    referenced_symbols: tuple[ReferencedSymbolContext, ...] = ()
    external_evidence: tuple[ExternalEvidenceContext, ...] = ()

    # 본문 크기 (build_context_step 결정론 추정) ─────────────
    token_count: int = 0                     # 본문 토큰 수

    # 의미 분석 결과 (analyze_code_step 이 채움) ──────────────
    summary: Optional[str] = None            # 사람이 읽는 표시용 의미 설명

    # Bottom-up summary dependency 메타 ───────────────────
    is_in_cycle: bool = False                # 결합 SCC 내부 → 순환 callee summary 없이 분석

    # strategy 별 확장 (자유 dict — 새 필드 추가 시 base 무변경) ────
    extra: dict[str, Any] = field(default_factory=dict)
