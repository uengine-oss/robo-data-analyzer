"""뷰 펼치기 (spec 112) — VIEW 를 향한 R/W 엣지에 기저 테이블 엣지를 추가한다.

코드가 뷰를 읽으면 지금은 "뷰까지만" 기록된다. 이 모듈은 step1이 CREATE VIEW
AST scope에서 보존한 ``Table.view_dependency_ids``를 따라 기저 테이블 READS를
뷰 경유 엣지로 추가한다.

- 문자열/regex 재파싱은 하지 않는다. schema/name quoted identity와 CTE-vs-physical
  구분은 producer AST가 소유한다.
- 뷰의 뷰(중첩)는 유한 view 집합의 방문집합 closure로 전개한다. 숫자 깊이 상한은 없다.
- 뷰를 향한 WRITES는 정의 query가 읽는 기저에 보수적으로 READS를 추가한다.
- 전략 무관 — 두 레인의 링킹 산출(ReadsWrites)에 동일 적용 (엣지 후처리, LLM 0).
"""
from __future__ import annotations

from graph.schema.graph_nodes import Table
from graph.schema.graph_edges import ReadsWrites


def expand_view_edges(
    edges: list[ReadsWrites], tables: list[Table],
) -> list[ReadsWrites]:
    """VIEW 대상 엣지 → 기저 테이블 추가 엣지 목록 (원본 엣지는 불변 — 뷰 직접 엣지 유지).

    반환 엣지의 evidence 는 원본 엣지의 것을 승계한다 (코드 위치가 근거,
    뷰 정의는 경유 사슬). 중복 (source, target, relation) 은 1개만.
    """
    views = {
        t.id: t for t in tables
        if t.table_type in ("VIEW", "MATERIALIZED_VIEW")
    }
    if not views:
        return []
    table_ids = {table.id for table in tables}

    def base_targets(view_id: str, visited: frozenset[str]) -> set[str]:
        """View ID → known physical leaf Table IDs (cycle-safe finite closure)."""
        if view_id in visited:
            return set()
        out: set[str] = set()
        for dependency_id in views[view_id].view_dependency_ids:
            if dependency_id == view_id or dependency_id not in table_ids:
                continue
            if dependency_id in views:
                out.update(base_targets(dependency_id, visited | {view_id}))
            else:
                out.add(dependency_id)
        return out

    added: list[ReadsWrites] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.target_id not in views:
            continue
        for base_fqn in sorted(base_targets(edge.target_id, frozenset())):
            relation = "READS"
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
