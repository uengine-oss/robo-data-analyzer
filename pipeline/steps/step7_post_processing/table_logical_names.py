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
