"""Project deterministic direct table-access facts for Summary context."""
from __future__ import annotations

from collections.abc import Mapping

from pipeline.models.semantic_frame import StructuralProfile


def project_direct_table_usages(
    profile: StructuralProfile,
    table_descriptions: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Return compact table facts without asking a model to restate them."""
    descriptions = table_descriptions or {}
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for reference in profile.data_object_refs:
        fact = profile.evidence[reference]
        table_id = str(fact.attributes.get("table_id") or "")
        access = str(fact.attributes.get("access") or "").upper()
        if (
            not table_id
            or access not in {"READ", "WRITE"}
            or not bool(fact.attributes.get("direct"))
            or fact.attributes.get("resolution") != "exact"
        ):
            continue
        occurrence: dict[str, object] = {"line": fact.start_line}
        operation = str(fact.attributes.get("operation") or "")
        if operation and operation.upper() != access:
            occurrence["operation"] = operation
        columns = fact.attributes.get("qualified_columns") or []
        if columns:
            occurrence["qualified_columns"] = columns
        grouped.setdefault((table_id, access.lower()), []).append(occurrence)

    projected: list[dict[str, object]] = []
    for (table_id, access_mode), occurrences in sorted(grouped.items()):
        item: dict[str, object] = {
            "table": table_id.removeprefix("db:"),
            "access_mode": access_mode,
            "occurrences": occurrences,
        }
        description = str(
            descriptions.get(table_id, {}).get("description") or ""
        ).strip()
        if description:
            item["table_description"] = description
        projected.append(item)
    return projected
