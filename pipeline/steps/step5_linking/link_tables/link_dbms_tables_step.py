"""step5 linking (DBMS 전용) — 모든 DML range → DDL 테이블 결정론 매칭.

DBMS 는 SQL 이 코드에 그대로 있다 — 각 노드의 **자기 줄**(자기 범위 − 직속 자식 줄범위)
본문에서 DDL 테이블 이름을 단어경계 문자열검색해 ``ReadsWrites`` 엣지를 만든다.
**AST 파싱·LLM 0.**

- DML 감지·write 대상 추출·dml_type 은 전부 **의미표**(``pipeline/dml_semantics.yaml``)에서
  파생한다 (spec 106 — 단일 진실). write 대상 = WRITES(그 구문의 dml_type), 그 외 = READS
  (본문 첫 DML 구문의 dml_type — 구 계약 유지).
- **모든 노드를 자기 줄만 스캔** — ``MERGE INTO …USING(SELECT…)`` 처럼 자식을 가진 부모
  노드도 자기 줄(바깥 문)에 INTO 대상·메인 FROM 이 있으므로 스캔한다. 자식 구간(서브쿼리)은
  그 자식 노드가 자기 줄로 따로 스캔 → **누락 0 · 중복 0** (각 테이블이 자기 줄이 속한
  노드에서 정확히 1 번). 자기 줄에 테이블 텍스트가 그대로 있어 ``EXECUTE IMMEDIATE`` 안
  리터럴 SQL 의 테이블도 자동 포착 (주석만 제거, 문자열은 보존 — TRUNCATE 포함).

(구 ``extract_dml_metadata_step`` 의 LLM 테이블 추출을 결정론으로 대체 — plan §M13.)
"""
from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.files.cache import SourceFileCache
from pipeline.dml_semantics import dml_semantics
from pipeline.languages import ROUTINE_LABELS
from pipeline.steps.step5_linking.link_tables.view_expansion import expand_view_edges
from pipeline.execution.step_config import step_config

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, ObjectReference, Table
from graph.nodes.object_reference import upsert_object_references
from pipeline.models.data_object_usage import DataObjectUsageEvidence, object_reference_id
from shared.events.runtime_logger import log_info
from graph.edges.reads_writes import upsert_reads_writes
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes

_STEP = "step5.link_dbms_tables"

# 주석 제거 (-- 라인 / /* */ 블록) — 주석 속 테이블명 오매칭 방지.
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
async def link_dbms_tables_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DBMS DML range → DDL 테이블 결정론 매칭 → ``ReadsWrites`` 엣지 (모든 노드 자기 줄)."""
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    code_nodes: list[CodeNode] = state.get("code_nodes") or []
    tables: list[Table] = state.get("tables") or []
    if not code_nodes:
        log_info(_STEP, "skip", reason="no code_nodes")
        return {}

    started = time.monotonic()
    log_info(_STEP, "start", code_nodes=len(code_nodes), tables=len(tables))
    # run 스코프 공유 캐시 (executor 주입) — 파일당 read 1회 (spec 046 FR-008).
    source_cache: SourceFileCache = cfg.get("source_cache") or SourceFileCache()
    datasource = (getattr(cfg.get("codebase"), "datasource", None) or "").strip()
    edges = _link(
        code_nodes, tables, state.get("parent_of_edges") or [], source_cache,
        datasource=datasource,
    )
    object_references = _unresolved_object_references(code_nodes, datasource)
    # 뷰 펼치기 (spec 112) — VIEW 엣지에 기저 테이블 엣지 추가 (원본 유지).
    view_edges = expand_view_edges(edges, tables)
    if view_edges:
        log_info(_STEP, "view_expansion", added=len(view_edges))
        edges = edges + view_edges

    if neo is not None:
        if object_references:
            await upsert_object_references(neo, object_references)
        if edges:
            await upsert_reads_writes(neo, edges)

    # 라이브 스트림 — R/W 링킹 결과를 step 완료 즉시 화면으로(관계 수 0 탈출).
    stream = cfg.get("stream")
    await emit_nodes(stream, object_references)
    await emit_edges(stream, edges)
    emit_message(stream, f"✅ 테이블 읽기/쓰기 링킹 완료 — {len(edges)}건")

    log_info(
        _STEP, "done",
        reads_writes_edges=len(edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"reads_writes_edges": edges, "object_references": object_references}


def _link(
    code_nodes: list[CodeNode], tables: list[Table], parent_of_edges: list,
    file_cache: SourceFileCache, *, datasource: str = "",
) -> list[ReadsWrites]:
    """모든 노드의 자기 줄(자기 범위 − 자식 줄범위) 에서 DDL 테이블명 검색 → ReadsWrites."""
    name_to_id = {t.name.upper(): t.id for t in tables}
    tables_by_name: dict[str, list[Table]] = {}
    for table in tables:
        tables_by_name.setdefault(table.name.upper(), []).append(table)
    # 긴 이름 우선 — 부분매칭 방지. 단어경계.
    table_re = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in sorted(name_to_id, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    ) if name_to_id else None
    # 각 노드의 직속 자식 줄범위 — 자기 줄 = 자기 범위에서 이 구간들을 뺀 나머지.
    # parent_of_edges 는 "스킵 대상" 이 아니라 "자기 줄 계산용" 으로만 쓴다.
    fn_by_id = {fn.id: fn for fn in code_nodes}
    child_ranges: dict[str, list[tuple[int, int]]] = {}
    for e in parent_of_edges:
        child = fn_by_id.get(e.child_id)
        if child is not None:
            child_ranges.setdefault(e.parent_id, []).append(
                (child.start_line, child.end_line)
            )

    edges: list[ReadsWrites] = []
    seen: set[tuple[str, str, str]] = set()
    semantics = dml_semantics()
    table_by_id = {table.id: table for table in tables}

    for fn in code_nodes:
        # DBMS reader 계약: 루틴은 선언 컨테이너이고 DML은 별도 CodeNode다. 루틴 root를
        # 재스캔하면 AFTER INSERT ON 같은 trigger event clause를 실행 SQL로 오인하고,
        # 하위 DML 누락도 숨긴다. 실제 statement 노드만 단일 경로로 링크한다.
        if fn.label in ROUTINE_LABELS:
            continue
        if fn.extra.get("data_object_evidence_version"):
            resolved_usages: list[DataObjectUsageEvidence] = []
            unresolved_usages: list[DataObjectUsageEvidence] = []
            for usage in fn.extra.get("data_object_usages") or ():
                scoped_usage = replace(usage, datasource=datasource)
                table = table_by_id.get(scoped_usage.table_id) \
                    if scoped_usage.resolution == "exact" and scoped_usage.table_id else None
                if table is None:
                    table = _resolve_ast_usage(scoped_usage, tables_by_name)
                if table is None:
                    resolved_usages.append(scoped_usage)
                    unresolved_usages.append(scoped_usage)
                    relation = "READS" if scoped_usage.access == "READ" else "WRITES"
                    target_id = object_reference_id(datasource, scoped_usage)
                    key = (fn.id, target_id, relation)
                    if key not in seen:
                        seen.add(key)
                        edges.append(ReadsWrites(
                            source_id=fn.id,
                            target_id=target_id,
                            relation=relation,
                            dml_type=semantics.dml_type_of(fn.label),
                            evidence_file=scoped_usage.evidence_file,
                            evidence_line=scoped_usage.evidence_line,
                        ))
                    continue
                resolved = scoped_usage.resolved(table.id)
                resolved_usages.append(resolved)
                relation = "READS" if scoped_usage.access == "READ" else "WRITES"
                key = (fn.id, table.id, relation)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(ReadsWrites(
                    source_id=fn.id,
                    target_id=table.id,
                    relation=relation,
                    dml_type=semantics.dml_type_of(fn.label),
                    evidence_file=scoped_usage.evidence_file,
                    evidence_line=scoped_usage.evidence_line,
                ))
            fn.extra["data_object_usages"] = tuple(resolved_usages)
            fn.extra["unresolved_data_object_usages"] = tuple(unresolved_usages)
            # Versioned parser evidence is complete for this grammar node. Never mix it with
            # the legacy bare-name source scanner, including when the exact result is empty.
            continue
        if table_re is None:
            continue
        body = _COMMENT_RE.sub(
            " ", _own_body(fn, child_ranges.get(fn.id, ()), file_cache),
        )
        if not body.strip():
            continue
        first_kw = semantics.first_dml(body)
        if first_kw is None:
            continue   # DML statement 아님 (ASSIGN/RAISE/CALL/컨테이너 골격 등)
        # write 대상 → 그 구문의 dml_type (TRUNCATE 대상은 TRUNCATE — spec 106 US1 AS-2).
        # READS 는 본문 첫 DML 구문의 dml_type (구 계약 유지 — FR-008).
        write_targets = semantics.write_target_tables(body)
        reads_dml_type = semantics.dml_type_of(first_kw)
        for m in table_re.finditer(body):
            name = m.group(1).upper()
            tid = name_to_id.get(name)
            if tid is None:
                continue
            relation = "WRITES" if name in write_targets else "READS"
            key = (fn.id, tid, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(ReadsWrites(
                source_id=fn.id,
                target_id=tid,
                relation=relation,
                dml_type=write_targets.get(name, reads_dml_type),
                evidence_file=fn.file_path,
                evidence_line=fn.start_line,
            ))
    return edges


def _unresolved_object_references(
    code_nodes: list[CodeNode], datasource: str,
) -> list[ObjectReference]:
    by_id: dict[str, ObjectReference] = {}
    for node in code_nodes:
        for usage in node.extra.get("unresolved_data_object_usages") or ():
            node_id = object_reference_id(datasource, usage)
            by_id.setdefault(node_id, ObjectReference(
                id=node_id,
                datasource=datasource,
                raw_reference=usage.raw_reference,
                schema=usage.schema,
                schema_quoted=usage.schema_quoted,
                name=usage.name,
                name_quoted=usage.name_quoted,
                database_link=usage.database_link,
            ))
    return [by_id[node_id] for node_id in sorted(by_id)]


def _resolve_ast_usage(
    usage: DataObjectUsageEvidence,
    tables_by_name: dict[str, list[Table]],
) -> Table | None:
    """Resolve only identities proven exact by the parser and local DDL set.

    A database link names a remote namespace that the local DDL datasource cannot prove. Bare
    names bind only when unique; schema-qualified names require an exact schema match.
    """
    if usage.database_link:
        return None
    candidates = list(tables_by_name.get(usage.name.upper(), ()))
    if usage.schema:
        candidates = [
            table for table in candidates
            if table.schema.upper() == usage.schema.upper()
        ]
    return candidates[0] if len(candidates) == 1 else None


def _own_body(fn: CodeNode, child_ranges, file_cache: SourceFileCache) -> str:
    """fn 의 자기 줄 텍스트 — 자기 ``[start, end]`` 에서 직속 자식 줄범위를 뺀 나머지 줄.

    부모 노드도 자기 줄(바깥 문: ``MERGE INTO …`` · 메인 ``FROM …``)만 본다 → 자식
    구간(서브쿼리)은 그 자식 노드가 따로 스캔. ``range_body._transform`` 의 line-walk 과
    같은 발상이되, 여기선 치환이 아니라 자식 줄 제거만 한다 (R/W 텍스트 스캔 입력).
    """
    lines = file_cache.lines(fn.file_path)
    if not lines:
        return ""
    end = fn.end_line if fn.end_line > 0 else len(lines)
    covered: set[int] = set()
    for cs, ce in child_ranges:
        for ln in range(cs, (ce if ce > 0 else cs) + 1):
            covered.add(ln)
    own = [
        lines[ln - 1]
        for ln in range(max(fn.start_line, 1), min(end, len(lines)) + 1)
        if ln not in covered
    ]
    return "\n".join(own)
