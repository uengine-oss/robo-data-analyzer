"""Minimal source evidence for model-owned branch Rules.

The deterministic layer supplies only coordinates, control-path polarity, and exact source
text.  It never composes Python ``cond`` or ``then`` answers.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pipeline.quality_gates.branch_inventory import BranchInventory


def render_condition_refs(
    terms: Sequence[str | Mapping[str, object] | object],
    condition_sources: Mapping[str, str],
) -> str | None:
    """Render structured model selections without interpreting source syntax."""
    clauses: list[str] = []
    for term in terms:
        if isinstance(term, str):
            holds = not term.startswith("!")
            ref = term[1:] if not holds else term
        elif isinstance(term, Mapping):
            ref = str(term.get("ref") or "")
            holds = bool(term.get("holds"))
        else:
            ref = str(getattr(term, "ref", ""))
            holds = bool(getattr(term, "holds", False))
        source = condition_sources.get(ref, ref)
        atom = f"F({json.dumps(source, ensure_ascii=False)})"
        clauses.append(atom if holds else f"not ({atom})")
    return " and ".join(clauses) or None


def _has_condition(inventory: BranchInventory, line: int) -> bool:
    """조건 원문이 실재하는 분기인가 — 판정절 없는 노드(`for(;;)` 등)는 False.

    grammar 가 조건을 두지 않는 decision 은 경로에 자기 항을 만들지 않는다.
    조건을 창작하면 안 되고(계약), 참조만 만들면 evidence 가 fatal 난다
    (framework 실측: zapamcom10200.c 1948 `for(;;)`).
    """
    branch = inventory.at_line(line)
    return branch is not None and bool(branch.condition.strip())


def _condition_path(
    inventory: BranchInventory,
    row: Mapping[str, object],
) -> list[tuple[int, bool]]:
    """Return source-condition coordinates and whether each must hold."""
    role = str(row.get("role") or "")
    references: dict[int, bool] = {}
    if role == "decision":
        branch = inventory.at_line(int(row["line"]))
        if branch is None:
            raise ValueError(f"decision row has no branch owner: {row['line']}")
        for ancestor in reversed(branch.ancestors()):
            ancestor_role = ancestor.prompt_role()
            line = ancestor.start + inventory.line_offset
            if ancestor_role == "decision":
                if _has_condition(inventory, line):
                    references[line] = True
            elif ancestor_role == "complement":
                paired = inventory.paired_decision(ancestor)
                if paired is not None:
                    paired_line = paired.start + inventory.line_offset
                    references.pop(paired_line, None)
                    references[paired_line] = False
        if _has_condition(inventory, int(row["line"])):
            references[int(row["line"])] = True
    elif role == "complement":
        context = row.get("complement_of")
        if isinstance(context, Mapping):
            direct = context.get("direct_negated")
            if isinstance(direct, int):
                references[direct] = False
            else:
                # controller 형상(switch default 등): 도달 조건은 주제식의 부정이 아니라
                # 형제 판정 전부의 불성립이다. 형제가 없으면 자기 항을 만들지 않는다
                # (조건 없는 decision 과 같은 계약 — 조건을 창작하지 않는다).
                for sibling in context.get("siblings") or ():
                    if isinstance(sibling, int) and _has_condition(inventory, sibling):
                        references[sibling] = False
            for outer in context.get("outer") or ():
                if not isinstance(outer, Mapping) or not isinstance(
                    outer.get("line"), int
                ):
                    continue
                state = str(outer.get("state") or "")
                if state in {"holding", "established", "negated"}:
                    if state != "negated" and not _has_condition(
                        inventory, int(outer["line"])
                    ):
                        continue
                    references[int(outer["line"])] = state != "negated"
    return list(references.items())


def _condition_source(
    inventory: BranchInventory,
    rows_by_line: Mapping[int, Mapping[str, object]],
    line: int,
) -> str:
    """Return exact source condition; never fabricate a fallback expression."""
    row = rows_by_line.get(line)
    if row is not None:
        source = str(row.get("test_condition") or row.get("condition") or "").strip()
        if source:
            return source
    branch = inventory.at_line(line)
    if branch is not None and branch.condition.strip():
        return branch.condition.strip()
    raise ValueError(f"branch condition source missing at line {line}")


def rule_evidence(
    inventory: BranchInventory,
    rows: Sequence[int],
) -> dict[str, object]:
    """Return each exact source fact once in a compact row-keyed object."""
    rows_by_line = {int(row["line"]): row for row in inventory.table_rows()}
    conditions: dict[str, str] = {}
    effects: dict[str, dict[str, object]] = {}
    effect_refs: dict[str, str] = {}
    row_payload: dict[str, dict[str, object]] = {}

    def effect_ref(payload: dict[str, object]) -> str:
        key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        known = effect_refs.get(key)
        if known is not None:
            return known
        ref = f"E{len(effects) + 1}"
        effects[ref] = payload
        effect_refs[key] = ref
        return ref

    for requested in rows:
        line = int(requested)
        row = rows_by_line.get(line)
        if row is None:
            raise ValueError(f"assigned row absent from branch inventory: {line}")
        path = _condition_path(inventory, row)
        for reference, _required in path:
            conditions[f"C{reference}"] = _condition_source(
                inventory, rows_by_line, reference,
            )
        direct_source, returns, assignments = inventory.direct_evidence(line)
        direct_effects = [
            effect_ref({
                "op": "call", "target": callee,
                "value": None, "from_source": None,
            })
            for callee in sorted(inventory.direct_callees(line))
        ]
        direct_effects.extend(
            effect_ref({
                "op": "ret", "target": None,
                "value": value, "from_source": True,
            })
            for value in sorted(returns)
        )
        direct_effects.extend(
            effect_ref({
                "op": "set", "target": target,
                "value": source, "from_source": True,
            })
            for target, source in assignments
        )
        # ontology statement_effect_ops 선언 효과(GOTO 점프 등) — 점프가 유일한
        # 효과인 분기도 고를 사실을 갖게 한다(2026-08-05 framework 실측).
        direct_effects.extend(
            effect_ref({
                "op": op, "target": target,
                "value": None, "from_source": None,
            })
            for op, target in inventory.direct_statement_effects(line)
        )
        # Repeated identical assignments can occur in parser evidence.  The global
        # effect dictionary already stores the payload once; keep the row reference
        # list equally canonical so the model never receives the same fact twice.
        direct_effects = list(dict.fromkeys(direct_effects))
        item: dict[str, object] = {
            "role": str(row.get("role") or ""),
            "when": [
                f"C{reference}" if required else f"!C{reference}"
                for reference, required in path
            ],
        }
        if direct_effects:
            item["direct"] = direct_effects
        source = direct_source.strip()
        referenced_sources = {conditions[f"C{reference}"] for reference, _ in path}
        if source and source not in referenced_sources:
            item["source"] = source
        row_payload[f"R{line}"] = item
    return {"conditions": conditions, "effects": effects, "rows": row_payload}


def expected_rule_condition(inventory: BranchInventory, line: int) -> str | None:
    """Return the exact structural path condition for one required row."""
    payload = rule_evidence(inventory, (line,))
    row = payload["rows"][f"R{line}"]
    return render_condition_refs(row["when"], payload["conditions"])


def rule_evidence_json(inventory: BranchInventory, rows: Sequence[int]) -> str:
    """Serialize :func:`rule_evidence` without repeating any source fact."""
    return json.dumps(
        rule_evidence(inventory, rows),
        ensure_ascii=False,
        separators=(",", ":"),
    )
