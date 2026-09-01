"""Minimal source evidence for model-owned branch Rules.

The deterministic layer supplies only coordinates, control-path polarity, and exact source
text.  It never composes Python ``cond`` or ``then`` answers.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pipeline.checks.branch_inventory import BranchInventory


def render_condition_refs(
    terms: Sequence[str],
    condition_sources: Mapping[str, str],
) -> str | None:
    """Render structured model selections without interpreting source syntax.

    ref 정규식은 모양만 닫혀 있어 증거에 없는 번호도 통과한다. 그 번호를 원문 자리에
    그대로 넣으면 ``F("C7")`` 이 되어 "원문이 C7 인 조건"과 구별되지 않는다. 효과 축의
    ``invalid_effect(...)`` 와 같은 계약으로 표면화하고 원문 후보를 보존한다.
    """
    clauses: list[str] = []
    for term in terms:
        holds = not term.startswith("!")
        ref = term if holds else term[1:]
        source = condition_sources.get(ref)
        if source is None:
            clauses.append(f"invalid_condition({json.dumps(term, ensure_ascii=False)})")
            continue
        atom = f"F({json.dumps(source, ensure_ascii=False)})"
        clauses.append(atom if holds else f"not ({atom})")
    return " and ".join(clauses) or None


def render_row_condition(
    row: Mapping[str, object],
    condition_sources: Mapping[str, str],
) -> str | None:
    """Materialize the exact structural condition for one evidence row."""
    base = render_condition_refs(row.get("when") or (), condition_sources)
    after = str(row.get("after_control") or "").strip()
    sequence = (
        f"after_control({json.dumps(after, ensure_ascii=False)})"
        if after else None
    )
    label = str(row.get("at_label") or "").strip()
    label_entry = (
        f"at_label({json.dumps(label, ensure_ascii=False)})"
        if label else None
    )
    return " and ".join(
        part for part in (base, sequence, label_entry) if part
    ) or None


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
    # handler 행은 두 형상으로 온다: skill 이 선언한 마커 줄(framework — 소유 branch 가
    # 없다)과 AST 핸들러 노드(PL/SQL EXCEPTION 섹션). 후자는 바깥 분기 안에 들어 있을 수
    # 있으므로 decision 과 **같은 조상 경로**를 갖는다 — 자기 항은 조건이 없어 붙지 않는다.
    if role in ("decision", "handler"):
        branch = inventory.at_line(int(row["line"]))
        if branch is None:
            if role == "decision":
                raise ValueError(f"decision row has no branch owner: {row['line']}")
            return []
        for ancestor in reversed(branch.ancestors()):
            ancestor_role = ancestor.prompt_role()
            line = ancestor.start + inventory.line_offset
            if ancestor_role == "decision":
                if _has_condition(inventory, line) and ancestor.condition_timing != "post":
                    references[line] = True
            elif ancestor_role == "complement":
                paired = inventory.paired_decision(ancestor)
                if paired is not None:
                    paired_line = paired.start + inventory.line_offset
                    references.pop(paired_line, None)
                    references[paired_line] = False
        if _has_condition(inventory, int(row["line"])) and branch.condition_timing != "post":
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
    elif role == "continuation":
        anchor = row.get("path_anchor_line")
        if isinstance(anchor, int):
            owner = next(
                (
                    candidate for candidate in inventory.table_rows()
                    if candidate.get("line") == anchor
                    and candidate.get("role") != "continuation"
                ),
                None,
            )
            if owner is None:
                raise ValueError(
                    f"continuation path owner missing: {row.get('line')} -> {anchor}"
                )
            references.update(_condition_path(inventory, owner))
        fallthrough = row.get("fallthrough_false_of")
        if isinstance(fallthrough, int) and _has_condition(inventory, fallthrough):
            references[fallthrough] = False
        for guard in row.get("sequence_guard_lines") or ():
            if isinstance(guard, int) and _has_condition(inventory, guard):
                references[guard] = False

    # 평면 대안 절 사슬 보정 (spec 118 §2.3). 자기와 조상 각각에 대해, 그것이 대안 절이면
    # 부모 판정과 앞선 형제 대안 절을 **부정**으로 확정한다. 위 계산이 부모 조건을
    # "성립"으로 상속했다면 여기서 뒤집힌다 — 좌표(키) 순서는 그대로라 표기만 정정된다.
    # 대안 절 타입이 없는 언어(C/Java/Python)는 이 루프가 아무것도 하지 않는다.
    owner = inventory.at_line(int(row["line"]))
    if owner is not None:
        for node in [owner, *owner.ancestors()]:
            for guard in node.sequence_guard_lines:
                guard_line = guard + inventory.line_offset
                if _has_condition(inventory, guard_line):
                    references[guard_line] = False
            for arm in inventory.negated_arms(node):
                arm_line = arm.start + inventory.line_offset
                if _has_condition(inventory, arm_line):
                    references[arm_line] = False
    return sorted(references.items())


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
        call_occurrences = inventory.direct_call_occurrences(line)
        direct_callees = inventory.direct_callees(line)
        if {item.callee.lower() for item in call_occurrences} != direct_callees:
            raise ValueError(
                f"Rule row call occurrences do not cover direct calls: line={line}"
            )
        direct_effects = [
            effect_ref({
                "op": "call",
                "target": occurrence.callee,
                "value": None,
                "from_source": None,
                "occurrence_id": occurrence.call_id,
                "call_source": occurrence.call_source,
                "argument_sources": list(occurrence.argument_sources),
                "receiver_source": occurrence.receiver_source,
                "callee_id": occurrence.callee_id,
            })
            for occurrence in call_occurrences
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
                "op": op, "target": target or None,
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
        # ``when`` is the complete parser-owned path used to materialize graph ``cond``.
        # It is not a request to spell every ancestor guard out in prose.  Expose the
        # one coordinate that distinguishes this branch separately so the model can
        # keep the narrative readable without losing the full structural condition.
        # A continuation has no new branch condition of its own: every ``when`` term
        # is inherited reachability and its narrative must prioritize ``direct``.
        item["narrative_focus"] = (
            []
            if str(row.get("role") or "") == "continuation"
            else item["when"][-1:]
        )
        if inventory.has_nested_control(line):
            item["nested_control"] = True
            owner = inventory.at_line(line)
            if owner is not None:
                # The parent row owns only the transition into its immediate child
                # controls.  Their coordinates/conditions explain that boundary
                # without copying any child action into the parent Rule.
                item["nested_children"] = [
                    {
                        "node_id": child.node_id,
                        "type": child.ntype,
                        "start_line": child.start + inventory.line_offset,
                        "end_line": child.end + inventory.line_offset,
                        "condition": child.condition,
                        "condition_timing": child.condition_timing,
                    }
                    for child in owner.children
                ]
        if row.get("fallthrough_to") is not None:
            item["fallthrough_to"] = int(row["fallthrough_to"])
        if str(row.get("role") or "") in {"continuation", "handler"}:
            item["after_control"] = str(row.get("after_control") or "")
            item["at_label"] = str(row.get("at_label") or "")
            if row.get("unresolved_reachability"):
                item["unresolved_reachability"] = True
        owner = inventory.at_line(line)
        if (
            owner is not None
            and owner.start + inventory.line_offset == line
            and owner.condition_timing == "post"
            and owner.condition.strip()
        ):
            item["post_condition"] = owner.condition.strip()
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
    return render_row_condition(row, payload["conditions"])


def rule_evidence_json(inventory: BranchInventory, rows: Sequence[int]) -> str:
    """Serialize :func:`rule_evidence` without repeating any source fact."""
    return json.dumps(
        rule_evidence(inventory, rows),
        ensure_ascii=False,
        separators=(",", ":"),
    )
