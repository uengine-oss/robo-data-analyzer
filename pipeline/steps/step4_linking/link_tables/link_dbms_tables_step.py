"""step4 linking (DBMS 전용) — 모든 DML range → DDL 테이블 결정론 매칭.

DBMS 는 SQL 이 코드에 그대로 있다 — 각 노드의 **자기 줄**(자기 범위 − 직속 자식 줄범위)
본문에서 DDL 테이블 이름을 단어경계 문자열검색해 ``ReadsWrites`` 엣지를 만든다.
**AST 파싱·LLM 0.**

- relation / dml_type : 본문 첫 DML 키워드 (SELECT/WITH=READS · INSERT/UPDATE/DELETE/MERGE=WRITES).
- write 대상 (INSERT INTO / UPDATE / DELETE FROM / MERGE INTO 직후 테이블) = WRITES, 그 외 = READS.
- **모든 노드를 자기 줄만 스캔** — ``MERGE INTO …USING(SELECT…)`` 처럼 자식을 가진 부모
  노드도 자기 줄(바깥 문)에 INTO 대상·메인 FROM 이 있으므로 스캔한다. 자식 구간(서브쿼리)은
  그 자식 노드가 자기 줄로 따로 스캔 → **누락 0 · 중복 0** (각 테이블이 자기 줄이 속한
  노드에서 정확히 1 번). 자기 줄에 테이블 텍스트가 그대로 있어 ``EXECUTE IMMEDIATE`` 안
  리터럴 SQL 의 테이블도 자동 포착 (주석만 제거, 문자열은 보존).

(구 ``extract_dml_metadata_step`` 의 LLM 테이블 추출을 결정론으로 대체 — plan §M13.)
"""
from __future__ import annotations

import re
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from source.file_cache import SourceFileCache
from pipeline.execution.step_config import step_config

from graph.schema.graph_edges import ReadsWrites, derive_dml_type
from graph.schema.graph_nodes import CodeNode, Table
from runtime_events.runtime_logger import log_info
from graph.edges.reads_writes import upsert_reads_writes
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_edges, emit_message

_STEP = "step4.link_dbms_tables"

# 첫 등장 DML 키워드 → dml_type. SELECT/WITH = READS, 나머지 = WRITES.
_DML_KW_RE = re.compile(r"\b(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)
# write 대상 테이블 — DML 키워드 직후 식별자 (스키마 prefix 허용, group 1 = 테이블명).
_WRITE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+(?:[\w$]+\.)?([A-Za-z_][\w$]*)",
    re.IGNORECASE,
)
# 주석 제거 (-- 라인 / /* */ 블록) — 주석 속 테이블명 오매칭 방지.
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


async def link_dbms_tables_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """DBMS DML range → DDL 테이블 결정론 매칭 → ``ReadsWrites`` 엣지 (모든 노드 자기 줄)."""
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    code_nodes: list[CodeNode] = state.get("code_nodes") or []
    tables: list[Table] = state.get("tables") or []
    if not code_nodes or not tables:
        log_info(_STEP, "skip", reason="no code_nodes or no DDL tables")
        return {}

    started = time.monotonic()
    log_info(_STEP, "start", code_nodes=len(code_nodes), tables=len(tables))
    # run 스코프 공유 캐시 (executor 주입) — 파일당 read 1회 (spec 046 FR-008).
    source_cache: SourceFileCache = cfg.get("source_cache") or SourceFileCache()
    edges = _link(code_nodes, tables, state.get("parent_of_edges") or [], source_cache)

    if neo is not None and edges:
        await upsert_reads_writes(neo, edges)

    # 라이브 스트림 — R/W 링킹 결과를 step 완료 즉시 화면으로(관계 수 0 탈출).
    stream = cfg.get("stream")
    await emit_edges(stream, edges)
    emit_message(stream, f"✅ 테이블 읽기/쓰기 링킹 완료 — {len(edges)}건")

    log_info(
        _STEP, "done",
        reads_writes_edges=len(edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"reads_writes_edges": edges}


def _link(
    code_nodes: list[CodeNode], tables: list[Table], parent_of_edges: list,
    file_cache: SourceFileCache,
) -> list[ReadsWrites]:
    """모든 노드의 자기 줄(자기 범위 − 자식 줄범위) 에서 DDL 테이블명 검색 → ReadsWrites."""
    name_to_id = {t.name.upper(): t.id for t in tables}
    if not name_to_id:
        return []
    # 긴 이름 우선 — 부분매칭 방지. 단어경계.
    table_re = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in sorted(name_to_id, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
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

    for fn in code_nodes:
        body = _COMMENT_RE.sub(
            " ", _own_body(fn, child_ranges.get(fn.id, ()), file_cache),
        )
        if not body.strip():
            continue
        dml_type = _first_dml(body)
        if dml_type is None:
            continue   # DML statement 아님 (ASSIGN/RAISE/CALL/컨테이너 골격 등)
        write_targets = {t.upper() for t in _WRITE_TARGET_RE.findall(body)}
        for m in table_re.finditer(body):
            tid = name_to_id.get(m.group(1).upper())
            if tid is None:
                continue
            relation = "WRITES" if m.group(1).upper() in write_targets else "READS"
            key = (fn.id, tid, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(ReadsWrites(
                source_id=fn.id,
                target_id=tid,
                relation=relation,
                dml_type=derive_dml_type(dml_type),
                evidence_file=fn.file_path,
                evidence_line=fn.start_line,
            ))
    return edges


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


def _first_dml(body: str) -> Optional[str]:
    """본문 첫 DML 키워드 → dml_type (대문자). DML statement 아니면 None."""
    m = _DML_KW_RE.search(body)
    return m.group(1).upper() if m else None
