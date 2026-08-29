"""테이블 insight에서 컬럼 설명·논리명 업데이트를 손실 없이 조립한다."""
from __future__ import annotations

from collections.abc import Mapping

from graph.schema.node_ids import fqn


def build_column_updates(
    table_candidates: Mapping[str, object],
    column_descriptions: Mapping[str, Mapping[str, str]],
    column_logicals: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    """description 또는 logical_name 중 하나만 있어도 해당 컬럼 업데이트를 보존한다."""
    updates: list[dict[str, str]] = []
    for table_id in table_candidates:
        names = set(column_descriptions.get(table_id, ())) | set(column_logicals.get(table_id, ()))
        for name in sorted(names):
            updates.append({
                "id": fqn(table_id, name),
                "analyzed_description": column_descriptions.get(table_id, {}).get(name, ""),
                "logical_name": column_logicals.get(table_id, {}).get(name, ""),
            })
    return updates


def build_consolidated_column_updates(
    table_ids: list[str],
    results: list[object],
    allowed_names: Mapping[str, set[str]],
) -> list[dict[str, str]]:
    """테이블별 최종 LLM 응답을 DDL 컬럼 집합과 정확히 대조한다.

    의미 선택은 여러 후보와 원래 설명을 함께 본 테이블 통합 호출이 담당한다. 이 함수는
    동적 schema 뒤에서도 None·누락·중복·과잉·빈 의미를 부분 성공으로 바꾸지 않는다.
    """
    if len(results) != len(table_ids):
        raise ValueError(
            f"table description result count mismatch: expected={len(table_ids)} "
            f"actual={len(results)}"
        )
    updates: list[dict[str, str]] = []
    for table_id, result in zip(table_ids, results):
        if result is None:
            raise ValueError(f"table description result is missing: table={table_id!r}")
        columns = list(getattr(result, "columns", ()) or ())
        names = [(getattr(column, "name", "") or "").strip() for column in columns]
        expected = set(allowed_names.get(table_id, set()))
        if any(not name for name in names):
            raise ValueError(f"table description contains an empty column name: {table_id!r}")
        if len(names) != len(set(names)):
            raise ValueError(f"table description contains duplicate columns: {table_id!r}")
        if set(names) != expected:
            raise ValueError(
                f"table description columns must match DDL exactly: table={table_id!r} "
                f"expected={sorted(expected)!r} actual={sorted(names)!r}"
            )
        for column, name in zip(columns, names):
            description = (getattr(column, "description", "") or "").strip()
            logical_name = (getattr(column, "logical_name", "") or "").strip()
            updates.append({
                "id": fqn(table_id, name),
                "analyzed_description": description,
                "logical_name": logical_name,
                "meaning_status": getattr(column, "evidence_status", ""),
            })
    return updates
