"""뷰 펼치기 (spec 112) — VIEW 를 향한 R/W 엣지에 기저 테이블 엣지를 추가한다.

코드가 뷰를 읽으면 지금은 "뷰까지만" 기록된다. 이 모듈은 뷰의 정의 SELECT
(``Table.view_definition`` — step1 이 보관)를 **DML 의미표(spec 106)** 로 다시 판정해
기저 테이블 READS 를 뷰 경유 엣지로 추가한다.

- 판정 로직은 의미표 하나 (sqlglot lineage 미사용 결정 — 방언 민감성·정의 재파싱이
  테이블 단위엔 과함. 컬럼 단위 요구가 생기면 sqlglot 로 승격, spec 문서 참조).
- 뷰의 뷰(중첩)는 깊이 상한까지 재귀. 순환은 방문집합으로 차단.
- 뷰를 향한 WRITES(updatable view)는 기저 **write 대상**에만 WRITES 전파, 그 외 READS.
- 전략 무관 — 두 레인의 링킹 산출(ReadsWrites)에 동일 적용 (엣지 후처리, LLM 0).
"""
from __future__ import annotations

from graph.schema.graph_nodes import Table
from graph.schema.graph_edges import ReadsWrites
from pipeline.dml_semantics import dml_semantics

_MAX_VIEW_DEPTH = 3


def expand_view_edges(
    edges: list[ReadsWrites], tables: list[Table],
) -> list[ReadsWrites]:
    """VIEW 대상 엣지 → 기저 테이블 추가 엣지 목록 (원본 엣지는 불변 — 뷰 직접 엣지 유지).

    반환 엣지의 evidence 는 원본 엣지의 것을 승계한다 (코드 위치가 근거,
    뷰 정의는 경유 사슬). 중복 (source, target, relation) 은 1개만.
    """
    views = {
        t.id: t for t in tables
        if t.table_type in ("VIEW", "MATERIALIZED_VIEW") and t.view_definition
    }
    if not views:
        return []
    lookup = {t.name.upper(): t.id for t in tables}
    semantics = dml_semantics()

    def base_targets(view_id: str, visited: frozenset[str], depth: int) -> dict[str, str]:
        """뷰 정의 → {기저 fqn: 'read'|'write'} (중첩 뷰 재귀, 순환·깊이 차단)."""
        if depth > _MAX_VIEW_DEPTH or view_id in visited:
            return {}
        definition = views[view_id].view_definition
        write_names = {
            m.group("entity").upper() for m, _ in semantics.iter_write_targets(definition)
        }
        read_names = {
            m.group("entity").upper()
            for m in semantics.read_target_regex.finditer(definition)
        }
        out: dict[str, str] = {}
        for name in read_names | write_names:
            fqn = lookup.get(name)
            if fqn is None or fqn == view_id:
                continue
            mode = "write" if name in write_names else "read"
            if fqn in views:                     # 뷰의 뷰 — 재귀 전개
                out.update(base_targets(fqn, visited | {view_id}, depth + 1))
            else:
                out[fqn] = mode
        return out

    added: list[ReadsWrites] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.target_id not in views:
            continue
        for base_fqn, mode in base_targets(edge.target_id, frozenset(), 1).items():
            # 코드가 뷰에 WRITES 했고 그 기저가 정의상 write 대상일 때만 WRITES 전파.
            relation = "WRITES" if (edge.relation == "WRITES" and mode == "write") else "READS"
            key = (edge.source_id, base_fqn, relation)
            if key in seen:
                continue
            seen.add(key)
            added.append(ReadsWrites(
                source_id=edge.source_id,
                target_id=base_fqn,
                relation=relation,
                dml_type=edge.dml_type if relation == edge.relation else "SELECT",
                evidence_file=edge.evidence_file,
                evidence_line=edge.evidence_line,
            ))
    return added
