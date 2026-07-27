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
    """테이블별 최종 LLM 응답에서 허용 컬럼만 한 번씩 업데이트한다.

    의미 선택은 여러 후보와 원래 설명을 함께 본 테이블 통합 호출이 담당한다. 이 함수는
    입력 허용 목록·중복만 결정론적으로 검증하며, 빠진 컬럼을 첫 후보나 sibling 값으로
    채우지 않는다.
    """
    updates: list[dict[str, str]] = []
    for table_id, result in zip(table_ids, results):
        if result is None:
            continue
        seen: set[str] = set()
        for column in getattr(result, "columns", ()) or ():
            name = (getattr(column, "name", "") or "").strip()
            if not name or name in seen or name not in allowed_names.get(table_id, set()):
                continue
            seen.add(name)
            description = (getattr(column, "description", "") or "").strip()
            logical_name = (getattr(column, "logical_name", "") or "").strip()
            if not description and not logical_name:
                continue
            updates.append({
                "id": fqn(table_id, name),
                "analyzed_description": description,
                "logical_name": logical_name,
            })
    return updates
