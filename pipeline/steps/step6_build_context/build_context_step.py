"""step6_build_context — 모든 CodeNode 에 ``AnalysisContext`` 생성.

흐름:
1. 코드노드 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
summary 슬롯은 None 으로 둠 → ``step7_analyze_code`` 이 Bottom-up DAG 로 채움.

이 단계는 LLM을 호출하지 않는다. ``SourceFileCache`` 가 file_path → 텍스트 라인을 캐싱한다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.files.cache import SourceFileCache
from shared.files.source_excerpt import raw_node_text
from pipeline.execution.step_config import step_config

from pipeline.steps.step6_build_context.control_subtree import (
    build_control_subtrees,
    build_statement_facts,
)
from pipeline.steps.step6_build_context.referenced_symbols import _build_reference_context
from pipeline.steps.step6_build_context.external_evidence import build_external_evidence_context
from pipeline.languages import ROUTINE_LABELS, callsite_labels, dml_effect_ops
from pipeline.quality_gates.evidence_matching import identity_leaf
from pipeline.models.analysis_context import AnalysisContext, CallOccurrenceFact
from pipeline.models.call_resolution import normalize_call_resolution_ledger
from pipeline.models.data_object_usage import (
    is_object_reference_id,
    object_reference_id,
    usages_from_linked_tables,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning

_STEP = "step6.build_context"


def _estimate_tokens(text: str) -> int:
    """본문 배치 예산용 보수적 토큰 추정."""
    n_ascii = sum(1 for char in text if ord(char) < 128)
    n_other = len(text) - n_ascii
    return n_ascii // 4 + int(n_other / 1.5)


def _owning_routine(node: CodeNode, by_id: dict[str, CodeNode]) -> CodeNode | None:
    """가장 가까운 routine 조상 (자신이 routine 이면 자신). 없으면 ``None``.

    ``control_subtree.build_statement_facts`` 와 같은 소유 계약이다 — 사이에 낀 비제어
    구조 노드(PL/SQL ``TRIGGER_BLOCK``·``DECLARE``)를 건너뛴다.
    """
    current: CodeNode | None = node
    while current is not None:
        if current.label in ROUTINE_LABELS:
            return current
        parent_id = current.parent_id
        current = by_id.get(parent_id) if parent_id else None
    return None


def _table_op_lines(
    rw_edges: list,
    code_nodes: list[CodeNode],
    unresolved_names: dict[str, str] | None = None,
) -> dict[str, list[tuple[int, str, str]]]:
    """소유 routine → ``(routine 상대 줄, 효과 op, 테이블명)`` (spec 119).

    step5 DBMS 링커는 **모든 CodeNode(=DML 노드 포함)의 자기 줄**을 스캔해
    ``ReadsWrites(source_id=그 노드, target_id=테이블, dml_type=구문 종류)`` 를 이미 만든다.
    즉 "몇 번째 줄이 어떤 테이블을 어떤 DML 로 건드리는가"는 결정론으로 존재하는데
    rule 증거까지 흐르지 않아, DBMS 규칙의 ``then`` 이 비어 있었다(59행 중 19행만 효과 보유,
    framework 는 83%).

    미선언 ``dml_type`` 은 조용히 버리지 않고 좌표와 함께 실패한다(헌법 IV).
    """
    by_id = {node.id: node for node in code_nodes}
    ops = dml_effect_ops()

    result: dict[str, set[tuple[int, str, str]]] = {}
    for edge in rw_edges:
        node = by_id.get(edge.source_id)
        dml = (getattr(edge, "dml_type", "") or "").upper()
        if node is None or not dml:
            continue
        # RULE의 DML 행 효과는 실제 statement CodeNode만 소유한다. Framework 링커는
        # SQL 문자열을 찾더라도 R/W를 루틴 노드에 달며, 그 관계는 ctx.reads/writes로 이미
        # 전달된다. 이를 DML statement처럼 승격하면 framework 증거 계약을 바꾸고 함수
        # 선언행을 가짜 entry로 만든다. 언어/전략명이 아니라 AST 소유 형태로 구분한다.
        if node.label in ROUTINE_LABELS:
            continue
        routine = _owning_routine(node, by_id)
        if routine is None:
            continue    # 모듈 레벨 — routine 효과가 아니다
        # **op 는 관계가 결정한다** (F-D2 계약). 엣지의 dml_type 은 그 구문의 사실이라
        # READS 엣지에도 쓰기 키워드가 실린다 — 트리거 헤더 `AFTER INSERT ON T` 의 대상
        # 테이블이 그렇다. 구문 dml 을 그대로 op 로 쓰면 읽기 테이블에 insert 보기가 붙어
        # 권위 table op(READ)와 충돌한다(2026-08-08 실측 finding).
        if edge.relation == "READS":
            op = "select"
        else:
            op = ops.get(dml)
        if op is None:
            raise ValueError(
                f"미선언 dml_type {dml!r} — {node.file_path}:{node.start_line} "
                f"({node.id}). ontology_roles.yaml 의 dml_effect_ops 에 선언하라 (spec 119)"
            )
        # 효과 좌표는 source CodeNode 의 시작점이 아니라 링커가 발견한 **실제 증거 줄**이
        # 소유한다. 현재 DBMS DML 노드에서는 두 값이 같지만, 좌표 계약은 관계의 증거가
        # 정본이다. 파생·확장 링커가 들어와도 노드 시작점으로 다시 추정하지 않는다.
        evidence_line = int(getattr(edge, "evidence_line", 0) or 0)
        if evidence_line <= 0:
            raise ValueError(
                f"DML 효과 evidence_line 부재 — {node.file_path}:{node.start_line} "
                f"({node.id}, {dml} {edge.target_id})"
            )
        line = evidence_line - routine.start_line + 1
        if line < 1 or evidence_line > routine.end_line:
            raise ValueError(
                f"DML 효과 좌표가 소유 routine 범위 밖 — evidence="
                f"{getattr(edge, 'evidence_file', '')}:{evidence_line}, routine="
                f"{routine.file_path}:{routine.start_line}-{routine.end_line} ({routine.id})"
            )
        # 규칙 어휘는 `insert("테이블")` — **원문 표기**다. 그래프 ID(`db:…`)를 그대로
        # 실으면 근거 검사(원문 문자열 대조)가 접두사에서 막히고, 규칙 카드에 내부
        # 식별자가 새어 나간다. 허용 집합 대조는 `resolve_qualified_name` 이 같은
        # leaf 정의로 흡수한다(2026-08-08 실측 21건).
        display_name = (unresolved_names or {}).get(
            edge.target_id, identity_leaf(edge.target_id),
        )
        result.setdefault(routine.id, set()).add((line, op, display_name))
    return {key: sorted(values) for key, values in result.items()}


def _data_object_identity_aliases(
    code_nodes: list[CodeNode], datasource: str,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Build unambiguous source spelling → graph target mappings per owning routine."""
    by_id = {node.id: node for node in code_nodes}
    candidates: dict[str, dict[str, set[str]]] = {}
    unresolved_names: dict[str, str] = {}
    for node in code_nodes:
        routine = _owning_routine(node, by_id)
        owner_ids = {node.id}
        if routine is not None:
            owner_ids.add(routine.id)
        for usage in node.extra.get("data_object_usages") or ():
            target_id = (
                usage.table_id
                if usage.resolution == "exact" and usage.table_id
                else object_reference_id(datasource, usage)
            )
            if is_object_reference_id(target_id):
                unresolved_names.setdefault(target_id, usage.raw_reference)
            spellings = {usage.raw_reference, usage.name}
            if usage.schema:
                spellings.add(f"{usage.schema}.{usage.name}")
            for owner_id in owner_ids:
                owner = candidates.setdefault(owner_id, {})
                for spelling in spellings - {""}:
                    owner.setdefault(spelling, set()).add(target_id)
    aliases = {
        owner_id: {
            spelling: next(iter(targets))
            for spelling, targets in by_spelling.items()
            if len(targets) == 1
        }
        for owner_id, by_spelling in candidates.items()
    }
    return aliases, unresolved_names


def _canonical_call_range(value: object) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    offset = value.get("char_offset", value.get("charOffset"))
    length = value.get("char_length", value.get("charLength"))
    if not isinstance(offset, int) or isinstance(offset, bool):
        return None
    if not isinstance(length, int) or isinstance(length, bool):
        return None
    return offset, length


def build_parser_call_evidence(
    call_sites: list[dict],
    call_resolution_ledger: list[dict],
    code_nodes: list[CodeNode],
) -> tuple[
    dict[str, list[tuple[int, str]]],
    dict[str, list[CallOccurrenceFact]],
]:
    """Join Parser call structure, Analyzer binding, and expression-owned context."""
    normalized_ledger = normalize_call_resolution_ledger(call_resolution_ledger)
    if len(call_sites) != len(normalized_ledger):
        raise ValueError(
            "parser call projection population mismatch: "
            f"sites={len(call_sites)}, resolutions={len(normalized_ledger)}"
        )
    site_ids = [str(site.get("call_id") or "") for site in call_sites]
    resolution_ids = [
        str(item.get("call_id") or "") for item in normalized_ledger
    ]
    if (
        not all(site_ids)
        or len(site_ids) != len(set(site_ids))
        or site_ids != resolution_ids
    ):
        raise ValueError("parser call projection requires exact ordered call IDs")
    routines = {
        fn.id: fn for fn in code_nodes if fn.label in ROUTINE_LABELS
    }
    call_lines_by_fn: dict[str, list[tuple[int, str]]] = {}
    call_occurrences_by_fn: dict[str, list[CallOccurrenceFact]] = {}
    accounted = 0
    for ordinal, (site, resolution) in enumerate(zip(
        call_sites, normalized_ledger, strict=True,
    )):
        call_id = site_ids[ordinal]
        caller = str(site.get("caller_id") or "")
        site_owner_status = str(site.get("owner_status") or "")
        owner_status = str(resolution.get("owner_status") or "")
        structural_pairs = (
            ("source_id", str(site.get("source_id") or ""), resolution["source_id"]),
            ("caller_id", caller, resolution["caller_id"]),
            ("owner_status", site_owner_status, owner_status),
            ("file", str(site.get("file") or ""), resolution["file"]),
            ("line", site.get("line"), resolution["line"]),
            ("callee_kind", site.get("callee_kind"), resolution["callee_kind"]),
            (
                "terminal_name", str(site.get("call_text") or ""),
                resolution["terminal_name"],
            ),
            (
                "grammar_rule", str(site.get("grammar_rule") or ""),
                resolution["grammar_rule"],
            ),
            (
                "presence_status", site.get("presence_status"),
                resolution["presence_status"],
            ),
            ("presence", site.get("presence"), resolution["presence"]),
        )
        mismatched = [
            name for name, parser_value, analyzer_value in structural_pairs
            if parser_value != analyzer_value
        ]
        if mismatched:
            raise ValueError(
                "parser call structure differs from resolution ledger: "
                f"call_id={call_id!r}, fields={mismatched}"
            )
        site_argument_ranges = site.get("argument_ranges")
        if not isinstance(site_argument_ranges, list):
            raise ValueError(
                f"invalid parser call argument ranges: call_id={call_id!r}"
            )
        range_pairs = (
            (
                "range", _canonical_call_range(site.get("range")),
                _canonical_call_range(resolution["range"]),
            ),
            (
                "callee_range",
                _canonical_call_range(site.get("callee_range")),
                _canonical_call_range(resolution["callee_range"]),
            ),
            (
                "argument_ranges",
                tuple(
                    _canonical_call_range(value) for value in site_argument_ranges
                ),
                tuple(
                    _canonical_call_range(value)
                    for value in resolution["argument_ranges"]
                ),
            ),
        )
        mismatched_ranges = [
            name for name, parser_value, analyzer_value in range_pairs
            if parser_value != analyzer_value
        ]
        if mismatched_ranges or any(
            value is None for value in (
                range_pairs[0][1], range_pairs[1][1], *range_pairs[2][1],
            )
        ):
            raise ValueError(
                "parser call ranges differ from resolution ledger: "
                f"call_id={call_id!r}, fields={mismatched_ranges}"
            )

        argument_sources = site.get("argument_sources")
        if (
            not isinstance(argument_sources, list)
            or len(argument_sources) != len(site_argument_ranges)
        ):
            raise ValueError(
                "parser call argument source/range population mismatch: "
                f"call_id={call_id!r}"
            )
        receiver_source = site.get("receiver_source")
        receiver_range = _canonical_call_range(site.get("receiver_range"))
        if (receiver_source is None) != (receiver_range is None):
            raise ValueError(
                "parser call receiver source/range presence mismatch: "
                f"call_id={call_id!r}"
            )
        raw_scope_path = site.get("scope_path")
        if not isinstance(raw_scope_path, list) or not raw_scope_path:
            raise ValueError(
                f"parser call scope path is missing: call_id={call_id!r}"
            )
        scope_path: list[tuple[str, tuple[int, int]]] = []
        for scope in raw_scope_path:
            if not isinstance(scope, dict):
                raise ValueError(
                    f"parser call scope path is invalid: call_id={call_id!r}"
                )
            kind = scope.get("kind")
            scope_range = _canonical_call_range(scope.get("range"))
            if (
                not isinstance(kind, str)
                or not kind
                or scope_range is None
                or scope_range[1] <= 0
            ):
                raise ValueError(
                    f"parser call scope path is invalid: call_id={call_id!r}"
                )
            if scope_path:
                parent_offset, parent_length = scope_path[-1][1]
                if not (
                    parent_offset <= scope_range[0]
                    and scope_range[0] + scope_range[1]
                    <= parent_offset + parent_length
                ):
                    raise ValueError(
                        "parser call scope nesting is invalid: "
                        f"call_id={call_id!r}"
                    )
            scope_path.append((kind, scope_range))
        call_offset, call_length = range_pairs[0][1]
        inner_offset, inner_length = scope_path[-1][1]
        if not (
            inner_offset <= call_offset
            and call_offset + call_length <= inner_offset + inner_length
        ):
            raise ValueError(
                f"parser call escapes scope path: call_id={call_id!r}"
            )
        if owner_status == "unresolved_no_routine":
            if caller:
                raise ValueError("ownerless call projection claims a caller")
            accounted += 1
            continue
        routine = routines.get(caller)
        if owner_status != "resolved" or routine is None:
            raise ValueError(
                f"resolved parser call owner is missing: {call_id!r}"
            )
        absolute_line = int(site.get("line") or 0)
        line = absolute_line - routine.start_line + 1
        if line < 1 or absolute_line > routine.end_line:
            raise ValueError(
                f"parser call range escapes routine: {call_id!r}"
            )
        call_source = site.get("call_source")
        callee_source = site.get("callee_source")
        callee_kind = str(resolution.get("callee_kind") or "")
        terminal_name = str(resolution.get("terminal_name") or "")
        name = terminal_name or str(callee_source or "")
        if (
            not isinstance(call_source, str)
            or not call_source
            or not isinstance(callee_source, str)
            or not callee_source
            or callee_kind not in {"named", "constructor", "expression"}
            or not name
        ):
            raise ValueError(
                f"incomplete parser call occurrence projection: call_id={call_id!r}"
            )
        if not all(isinstance(value, str) for value in argument_sources):
            raise ValueError(
                f"invalid parser call argument source: call_id={call_id!r}"
            )
        if receiver_source is not None and not isinstance(receiver_source, str):
            raise ValueError(
                f"invalid parser call receiver source: call_id={call_id!r}"
            )
        offset, length = range_pairs[0][1]
        if offset < 0 or length <= 0:
            raise ValueError(f"parser call range is invalid: call_id={call_id!r}")
        condition_owners = []
        for fact in routine.extra.get("_resolved_expression_facts") or ():
            if str(fact.get("role") or "") not in {"condition", "case_value"}:
                continue
            fact_range = fact.get("source_range") or {}
            fact_offset = int(fact_range.get("char_offset") or 0)
            fact_length = int(fact_range.get("char_length") or 0)
            if (
                fact_length > 0
                and fact_offset <= offset
                and offset + length <= fact_offset + fact_length
            ):
                condition_owners.append(str(fact.get("fact_id") or ""))
        if len(condition_owners) > 1 or any(not value for value in condition_owners):
            raise ValueError(
                f"parser call condition ownership is ambiguous: call_id={call_id!r}"
            )
        presence_status = str(resolution.get("presence_status") or "")
        if presence_status not in {"active", "inactive", "conditional", "unknown"}:
            raise ValueError(f"parser call presence is invalid: call_id={call_id!r}")
        context_role = (
            "non_active"
            if presence_status != "active"
            else "condition" if condition_owners else "effect"
        )
        occurrence = CallOccurrenceFact(
                call_id=call_id,
                line=line,
                callee=name.lower(),
                call_source=call_source,
                argument_sources=tuple(argument_sources),
                receiver_source=receiver_source,
                source_range=(offset, length),
                callee_range=range_pairs[1][1],
                argument_ranges=range_pairs[2][1],
                receiver_range=receiver_range,
                scope_path=tuple(scope_path),
                grammar_rule=str(resolution.get("grammar_rule") or ""),
                callee_source=callee_source,
                callee_kind=callee_kind,
                context_role=context_role,
                condition_fact_id=(condition_owners[0] if condition_owners else None),
                presence_status=presence_status,
                resolution_status=str(resolution.get("resolution_status") or ""),
                resolution_outcome=str(resolution.get("outcome") or ""),
                resolution_scope=resolution.get("scope"),
                resolution_reason=str(resolution.get("reason") or ""),
                callee_id=str(resolution.get("callee_id") or ""),
                external_identity_key=str(
                    resolution.get("external_identity_key") or ""
                ),
            )
        if context_role != "non_active":
            call_lines_by_fn.setdefault(caller, []).append((line, occurrence.callee))
        call_occurrences_by_fn.setdefault(caller, []).append(
            occurrence
        )
        accounted += 1
    if accounted != len(call_sites):
        raise ValueError("parser call projection is not exact-once")
    for lines in call_lines_by_fn.values():
        lines.sort()
    for occurrences in call_occurrences_by_fn.values():
        occurrences.sort(key=lambda item: (item.line, item.call_id))
    return call_lines_by_fn, call_occurrences_by_fn


async def build_context_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """모든 CodeNode 에 ``AnalysisContext`` 생성."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    started = time.monotonic()

    builder = strategy.context_builder
    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    rw_edges = state.get("reads_writes_edges", [])
    calls_edges = state.get("calls_edges", [])
    log_info(
        _STEP, "start",
        code_nodes=len(code_nodes), reads_writes_edges=len(rw_edges), calls_edges=len(calls_edges),
        builder=type(builder).__name__,
    )

    # code_id → reads/writes/callers/callees 집계 (sorted tuple 강제 — 캐시 친화)
    reads_by_fn: dict[str, set[str]] = {}
    writes_by_fn: dict[str, set[str]] = {}
    direct_reads_by_fn: dict[str, set[str]] = {}
    direct_writes_by_fn: dict[str, set[str]] = {}
    # 권위 table op 의 재료 — **관계가 op 를 결정한다**. 엣지의 dml_type 은 그 구문의
    # 사실이라(INSERT…SELECT 의 읽기 테이블, 트리거 헤더 `AFTER INSERT ON T` 의 대상
    # 테이블도 구문 dml 은 INSERT) READS 엣지의 dml 을 테이블 op 로 승격하면 읽기
    # 테이블이 쓰기 op 를 얻어 스키마 소유권 검사와 충돌한다(2026-08-07 rwis 실측 fatal).
    # 따라서 WRITES 엣지의 dml 만 op 후보로 모으고, 읽기 테이블은 READ 로 확정한다.
    write_ops_by_fn: dict[str, dict[str, set[str]]] = {}
    read_dml_tables_by_fn: dict[str, set[str]] = {}
    # **루틴의 R/W 는 자기 자손 구문이 하는 R/W 를 포함한다.** DBMS 는 DML 이 루틴의
    # 자식 CodeNode 라 링커가 그 노드에 엣지를 단다(그래프 계약은 그대로 유지). 그런데
    # rule 의무 행의 소유자는 루틴이므로(spec 117), 루틴의 허용 테이블이 자기 줄만 담으면
    # 자손 DML 에서 온 정당한 효과가 "결정론 R/W 집합에 없음"으로 거절된다
    # (2026-08-08 실측: findings 21건 전부 이 원인). 자손 구문은 callee 가 아니라 그 루틴의
    # 본문이므로 04 §3.2 의 "callee R/W 오귀속 금지"에 걸리지 않는다.
    # framework 는 링커가 함수 노드에 직접 달아 자손이 없으므로 결과가 달라지지 않는다.
    nodes_by_id = {node.id: node for node in code_nodes}
    for e in rw_edges:
        is_read = e.relation == "READS"
        direct_bucket = direct_reads_by_fn if is_read else direct_writes_by_fn
        direct_bucket.setdefault(e.source_id, set()).add(e.target_id)
        owner = nodes_by_id.get(e.source_id)
        routine = _owning_routine(owner, nodes_by_id) if owner is not None else None
        owner_ids = {e.source_id}
        if routine is not None:
            owner_ids.add(routine.id)
        for owner_id in owner_ids:
            bucket = reads_by_fn if is_read else writes_by_fn
            bucket.setdefault(owner_id, set()).add(e.target_id)
        # **권위 table op 는 집계하지 않는다** — 자기 줄 사실로만 정한다. 한 루틴이 같은
        # 테이블을 어떤 구문에서는 읽고 어떤 구문에서는 쓰면 루틴 단위 op 가 하나로
        # 뭉개져 정당한 `select` 가 "table op MERGE와 다름"으로 거절된다(2026-08-08 실측).
        # op 의 진실은 행 단위 `ast_table_ops` 가 이미 갖고 있다.
        dml = getattr(e, "dml_type", "") or ""
        if not dml:
            continue
        if is_read:
            read_dml_tables_by_fn.setdefault(e.source_id, set()).add(e.target_id)
        else:
            write_ops_by_fn.setdefault(e.source_id, {}).setdefault(
                e.target_id, set()
            ).add(dml)

    callers_by_fn: dict[str, set[str]] = {}
    callees_by_fn: dict[str, set[str]] = {}
    for e in calls_edges:
        callees_by_fn.setdefault(e.caller_id, set()).add(e.callee_id)
        callers_by_fn.setdefault(e.callee_id, set()).add(e.caller_id)

    # 참조 컨텍스트 — REFERENCES + INIT_BY (참조 변수/상수).
    refsym_by_fn = _build_reference_context(state)
    external_by_fn = build_external_evidence_context(state, code_nodes)

    # 파일 본문 캐시 — 같은 file 여러 코드노드가 공유 (성능: N*M read → N read).
    body_cache = cfg.get("source_cache") or SourceFileCache()   # run 스코프 공유 (FR-008)

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    control_subtrees = build_control_subtrees(code_nodes)
    statement_facts = build_statement_facts(
        code_nodes, state.get("variables", []), state.get("constants", []),
    )
    # parser FUNCTION_CALL 좌표 (spec 016) — branch 직접 호출 귀속의 AST 정본.
    # (routine 상대 1-based line, 호출명) 쌍. source 정규식 `이름(` 재탐색을 대체한다.
    call_lines_by_fn, call_occurrences_by_fn = build_parser_call_evidence(
        list(state.get("call_sites", []) or []),
        list(state.get("call_resolution_ledger", []) or []),
        code_nodes,
    )
    # CodeNode 자체가 호출 자리인 언어(DBMS `CALL`)의 좌표 — ontology 가 라벨을 소유한다.
    # framework 는 이 집합이 비어 있어 아무것도 더해지지 않는다(증거 불변, spec 119).
    node_callsite_labels = callsite_labels()
    if node_callsite_labels:
        nodes_by_id = {node.id: node for node in code_nodes}
        for node in code_nodes:
            if node.label not in node_callsite_labels or not node.name:
                continue
            routine = _owning_routine(node, nodes_by_id)
            if routine is None:
                continue
            line = node.start_line - routine.start_line + 1
            if line >= 1:
                call_lines_by_fn.setdefault(routine.id, []).append((line, node.name))
    for lines in call_lines_by_fn.values():
        lines.sort()
    datasource = (getattr(cfg.get("codebase"), "datasource", None) or "").strip()
    table_aliases_by_fn, unresolved_names = _data_object_identity_aliases(
        code_nodes, datasource,
    )
    table_ops_by_fn = _table_op_lines(rw_edges, code_nodes, unresolved_names)
    for fn in code_nodes:
        # 원문은 step2 가 이미 채웠다 (code_text) — 같은 (file,start,end) 를 여기서 다시
        # 잘라내지 않는다 (spec 046 FR-007: 슬라이싱 계산은 한 곳). 없을 때만 캐시에서 슬라이스
        # (code_text 가 빈 경우 = 소스 read 실패 — FR-004 가 이미 경고를 남겼다).
        body_text = raw_node_text(fn) or body_cache.slice(
            fn.file_path, fn.start_line, fn.end_line
        )
        ctx = AnalysisContext(
            id=fn.id,
            signature=fn.signature,
            file_path=fn.file_path,
            start_line=fn.start_line,
            end_line=fn.end_line,
            reads=tuple(sorted(reads_by_fn.get(fn.id, ()))),
            writes=tuple(sorted(writes_by_fn.get(fn.id, ()))),
            direct_reads=tuple(sorted(direct_reads_by_fn.get(fn.id, ()))),
            direct_writes=tuple(sorted(direct_writes_by_fn.get(fn.id, ()))),
            callers=tuple(sorted(callers_by_fn.get(fn.id, ()))),
            callees=tuple(sorted(callees_by_fn.get(fn.id, ()))),
            referenced_symbols=tuple(sorted(refsym_by_fn.get(fn.id, ()), key=lambda symbol: (symbol.kind, symbol.name, symbol.id))),
            external_evidence=external_by_fn.get(fn.id, ()),
            token_count=_estimate_tokens(body_text),
        )
        ctx = await builder.augment(ctx, fn, state)
        if fn.extra.get("data_object_evidence_version"):
            ctx.extra["data_object_evidence_version"] = fn.extra["data_object_evidence_version"]
            ctx.extra["data_object_usages"] = tuple(fn.extra.get("data_object_usages") or ())
            ctx.extra["unresolved_qualified_columns"] = tuple(
                fn.extra.get("unresolved_qualified_columns") or ()
            )
            if int(fn.extra.get("data_object_evidence_version") or 0) >= 2:
                ctx.extra["unqualified_identifier_references"] = tuple(
                    fn.extra.get("unqualified_identifier_references") or ()
                )
            ctx.extra["unresolved_data_object_usages"] = tuple(
                fn.extra.get("unresolved_data_object_usages") or ()
            )
        if fn.extra.get("source_language"):
            ctx.extra["source_language"] = fn.extra["source_language"]
        else:
            # Framework and legacy DBMS edges enter the same prompt contract with explicit
            # linked-edge provenance; no alias/column facts are invented.
            ctx.extra["data_object_usages"] = usages_from_linked_tables(
                source_id=fn.id,
                edges=rw_edges,
            )
        if table_aliases_by_fn.get(fn.id):
            ctx.extra["table_identity_aliases"] = table_aliases_by_fn[fn.id]
        if fn.id in control_subtrees:
            ctx.extra["control_subtree"] = control_subtrees[fn.id]
        # parser 소유 구조 statement 정본 (spec 016) — branch_inventory 가 종료행·
        # 반환식·대입·entry 를 이것으로만 조립한다. statement 가 없는 routine 도
        # "없음"이 사실이므로 빈 튜플을 명시 제공한다(source 재탐색 금지).
        ctx.extra["ast_statements"] = tuple(statement_facts.get(fn.id, ()))
        ctx.extra["ast_call_lines"] = tuple(call_lines_by_fn.get(fn.id, ()))
        ctx.extra["ast_call_occurrences"] = tuple(
            call_occurrences_by_fn.get(fn.id, ())
        )
        # DBMS DML 효과 좌표 (spec 119) — step5 링커가 DML 노드마다 이미 만든
        # (테이블, dml_type) 을 소유 routine 상대 줄로 옮긴다. framework 는 DML 노드가
        # 없어 항상 빈 튜플이며 증거가 달라지지 않는다.
        table_op_lines = tuple(table_ops_by_fn.get(fn.id, ()))
        if table_op_lines:
            ctx.extra["ast_table_ops"] = table_op_lines
        # 권위 op 맵 — LLM 이 낡은 주석에 속아 op 를 뒤바꾸는 환각(감사 최대 결함)을
        # 결정론으로 교정하는 근거. 쓰기 테이블은 **단일 write dml** 일 때만 그 op
        # (모호하면 제외 — READ 로 격하하지 않는다), 쓰기 dml 이 없는 읽기 테이블은
        # 폐쇄형 op "READ"(AFFECTS_TABLE 계약)로 확정한다.
        fn_write_ops = write_ops_by_fn.get(fn.id, {})
        table_op = {
            table: next(iter(ops))
            for table, ops in fn_write_ops.items() if len(ops) == 1
        }
        for table in read_dml_tables_by_fn.get(fn.id, ()):
            if table not in fn_write_ops:
                table_op[table] = "READ"
        if table_op:
            ctx.extra["table_op"] = table_op
        contexts[fn.id] = ctx

    log_info(
        _STEP, "done",
        contexts=len(contexts),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}
