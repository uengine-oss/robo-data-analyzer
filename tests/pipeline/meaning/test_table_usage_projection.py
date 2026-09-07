from __future__ import annotations

from pipeline.meaning.table.table_usage_projection import project_direct_table_usages
from pipeline.models.semantic_frame import (
    EvidenceFact,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
)


def _profile() -> StructuralProfile:
    target_id = "code:sample.c:work"
    target = StructuralTarget(
        id=target_id,
        type="FUNCTION",
        file="sample.c",
        start_line=10,
        end_line=30,
    )
    target_ref = evidence_id(target_id, "TARGET", 0)
    facts = {
        target_ref: EvidenceFact(
            id=target_ref,
            kind="TARGET",
            owner_id=target_id,
            file="sample.c",
            start_line=10,
            end_line=30,
            attributes={"type": "FUNCTION", "name": "work"},
        ),
    }
    refs: list[str] = []
    for line, table_id, access, direct, resolution in (
        (12, "db:public.orders", "READ", True, "exact"),
        (18, "db:public.orders", "READ", True, "exact"),
        (21, "db:public.audit", "WRITE", False, "exact"),
        (25, "db:public.users", "READ", True, "ambiguous"),
    ):
        ref = evidence_id(target_id, "DATA_OBJECT", [line, table_id, access])
        refs.append(ref)
        facts[ref] = EvidenceFact(
            id=ref,
            kind="DATA_OBJECT",
            owner_id=target_id,
            file="sample.c",
            start_line=line,
            end_line=line,
            attributes={
                "table_id": table_id,
                "access": access,
                "operation": "SELECT" if access == "READ" else "UPDATE",
                "direct": direct,
                "resolution": resolution,
            },
        )
    return StructuralProfile(target=target, evidence=facts, data_object_refs=refs)


def test_projection_keeps_only_exact_direct_facts_without_model_restating() -> None:
    projected = project_direct_table_usages(
        _profile(),
        {"db:public.orders": {"description": "주문"}},
    )

    assert projected == [{
        "table": "public.orders",
        "access_mode": "read",
        "occurrences": [
            {"line": 12, "operation": "SELECT"},
            {"line": 18, "operation": "SELECT"},
        ],
        "table_description": "주문",
    }]
