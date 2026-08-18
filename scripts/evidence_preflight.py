"""run 없이 결정론 증거를 재는 상설 도구 (04 §4.5 — 일회용 스크립트 금지).

LLM 호출 0 · Neo4j 0 · **재실행 상한을 소진하지 않는다.** 생산 경로를 그대로 태워
"지금 코퍼스에서 의무 행이 몇 개 나오고 그중 몇 개가 효과 증거를 갖는가"를 잰다.
프롬프트·증거 조립을 고친 뒤 cold run 을 태우기 **전에** 반드시 이것으로 먼저 잰다.

두 가지 모드:

``measure``  현재 ``project\\data`` 코퍼스의 의무 회계와 효과 커버리지 (기본)
``diff``     저장된 run 프롬프트의 evidence ↔ 지금 코드가 만드는 evidence 행 단위 대조.
             **산출물 무변경 증명**의 도구다 — 코드를 고쳤는데 증거가 그대로임을 보인다.

사용::

    python scripts/evidence_preflight.py measure
    python scripts/evidence_preflight.py measure --data-dir D:/work/robo/project/data
    python scripts/evidence_preflight.py diff logs/<run_id> --corpus <framework 코퍼스 루트>

``--corpus`` 는 그 run 이 쓴 코퍼스다. run 폴더의 ``manifest.json`` 이 기록한
``corpus_root`` 가 그 뒤 교체됐을 수 있으므로 명시할 수 있게 열어 둔다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS, callsite_labels, detect_strategy, lang_of
from pipeline.quality_gates.branch_inventory import (
    build_inventory,
    validate_static_jump_targets,
)
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step1_parse_ddl.ddl_parser import parse_ddl_file
from pipeline.steps.step2_load_ast.dbms_reader import _read_dbms
from pipeline.steps.step2_load_ast.framework_reader import _read_framework
from pipeline.steps.step2_load_ast.load_ast_step import ast_json_paths
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import _link
from pipeline.steps.step5_linking.link_calls.call_linker import CallLinker
from pipeline.steps.step6_build_context.build_context_step import (
    _data_object_identity_aliases, _owning_routine, _table_op_lines,
)
from pipeline.steps.step6_build_context.control_subtree import (
    build_control_subtrees, build_statement_facts,
)
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records
from pipeline.steps.step7_analyze_code.analysis_order import (
    bottom_up_rounds,
    plan_summary_dependencies,
)
from pipeline.steps.step7_analyze_code.batch_planner import build_effective_tree
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _table_target_ids,
)
from pipeline.models.analysis_context import AnalysisContext
from shared.config.settings import SETTINGS
from shared.files.cache import SourceFileCache

# framework skill 이 선언하는 handler 마커. scan agent 가 run 에서 주입하는 값과 같은
# 것이어야 handler 행(67개)이 재현된다 — 빠뜨리면 검사기가 제품을 오판한다(실측).
_FRAMEWORK_HANDLER_MARKERS = ("PFM_CATCH",)


class _Corpus:
    """한 코퍼스의 결정론 조립 결과 — 루틴별 분기표와 증거."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.source = data_dir / "source"
        self.analysis = data_dir / "analysis"
        self.strategy = detect_strategy(self.source)
        self.cache = SourceFileCache()
        self.nodes: list[CodeNode] = []
        self.modules: list = []
        self.variables: list = []
        self.constants: list = []
        self.import_refs: list[dict] = []
        self.parent_edges: list = []
        self._load()

    @property
    def lang(self) -> str:
        return "sql" if self.strategy == "dbms" else "c"

    def _load(self) -> None:
        # Keep the safety measurement on the exact same corpus boundary as
        # step2_load_ast: a target's parser output may be nested below
        # analysis/<target>/... and must not disappear from the preflight.
        for path in ast_json_paths(self.analysis):
            raw = json.loads(path.read_text(encoding="utf-8"))
            result = (
                _read_dbms(raw, self.source, path)
                if self.strategy == "dbms"
                else _read_framework(raw, self.lang, self.source, path)
            )
            self.nodes += result.code_nodes
            self.modules += result.modules
            self.variables += result.variables
            self.constants += result.constants
            self.parent_edges += result.parent_of_edges
            self._call_sites = getattr(self, "_call_sites", []) + list(result.call_sites)
            self.import_refs += result.import_refs

    def _call_lines(self) -> dict[str, list[tuple[int, str]]]:
        """(routine 상대 줄, 호출명) — reader call_site + CodeNode 형 호출 자리."""
        by_id = {node.id: node for node in self.nodes}
        start_by_id = {node.id: node.start_line for node in self.nodes}
        lines: dict[str, list[tuple[int, str]]] = {}
        for site in getattr(self, "_call_sites", ()):
            caller = str(site.get("caller_id") or "")
            start = start_by_id.get(caller)
            name = str(site.get("call_text") or "")
            if not caller or start is None or not name:
                continue
            line = int(site.get("line") or 0) - start + 1
            if line >= 1:
                lines.setdefault(caller, []).append((line, name))
        labels = callsite_labels()
        for node in self.nodes:
            if node.label not in labels or not node.name:
                continue
            routine = _owning_routine(node, by_id)
            if routine is None:
                continue
            line = node.start_line - routine.start_line + 1
            if line >= 1:
                lines.setdefault(routine.id, []).append((line, node.name))
        for value in lines.values():
            value.sort()
        return lines

    def _table_ops(self) -> dict[str, list[tuple[int, str, str]]]:
        if self.strategy != "dbms":
            return {}
        tables = []
        for path in sorted((self.data_dir / "ddl").glob("*.sql")):
            tables += parse_ddl_file(path, db=self.data_dir.name).tables
        edges = _link(self.nodes, tables, self.parent_edges, self.cache)
        # Product step6 converts unresolved OBJECT_REFERENCE graph ids back to their exact
        # source spelling before they enter RULE evidence.  Reproduce that boundary here;
        # comparing an internal sha256 id with the saved ``DUAL``/``ALL_OBJECTS`` spelling
        # is a checker-coordinate error, not a semantic evidence change.
        _aliases, unresolved_names = _data_object_identity_aliases(self.nodes, "")
        return _table_op_lines(edges, self.nodes, unresolved_names)

    def evidence(self) -> dict[str, dict]:
        """routine id → rule evidence rows."""
        return {
            target: packet["rows"]
            for target, packet in self.evidence_packets().items()
        }

    def evidence_packets(self) -> dict[str, dict]:
        """routine id → complete rule evidence packet, including dereference tables."""
        subtrees = build_control_subtrees(self.nodes)
        facts = build_statement_facts(self.nodes, self.variables, self.constants)
        call_lines = self._call_lines()
        table_ops = self._table_ops()
        markers = () if self.strategy == "dbms" else _FRAMEWORK_HANDLER_MARKERS
        out: dict[str, dict] = {}
        for fn in self.nodes:
            if fn.label not in ROUTINE_LABELS:
                continue
            inventory = build_inventory(
                self.cache.slice(fn.file_path, fn.start_line, fn.end_line),
                subtrees.get(fn.id), lang_of(Path(fn.file_path).suffix),
                line_offset=max(fn.start_line - 1, 0),
                handler_markers=markers,
                statements=tuple(facts.get(fn.id, ())),
                call_lines=tuple(call_lines.get(fn.id, ())),
                table_ops=tuple(table_ops.get(fn.id, ())),
            )
            rows = required_rule_rows(inventory)
            if not rows:
                out[fn.id] = {"conditions": {}, "effects": {}, "rows": {}}
                continue
            out[fn.id] = rule_evidence(inventory, tuple(rows))
        return out

    def static_jump_summary(self) -> dict[str, int]:
        """Validate and count parser-owned active named jumps before other evidence."""
        facts = build_statement_facts(self.nodes, self.variables, self.constants)
        gotos = 0
        labels: set[tuple[str, str, int]] = set()
        routines = 0
        for fn in self.nodes:
            if fn.label not in ROUTINE_LABELS:
                continue
            routines += 1
            resolutions = validate_static_jump_targets(
                self.cache.slice(fn.file_path, fn.start_line, fn.end_line),
                lang_of(Path(fn.file_path).suffix),
                tuple(facts.get(fn.id, ())),
                line_offset=max(fn.start_line - 1, 0),
                target_id=fn.id,
                source_ref=fn.file_path,
            )
            gotos += len(resolutions)
            labels.update(
                (fn.id, resolution.target, resolution.label_line)
                for resolution in resolutions
            )
        return {
            "routines": routines,
            "resolved_gotos": gotos,
            "target_labels": len(labels),
        }

    def dbms_summary_dependencies(self) -> dict[str, object]:
        """Reproduce DBMS CALL summary ownership and rounds without a run."""
        if self.strategy != "dbms":
            raise ValueError("callee-summary preflight currently requires a DBMS corpus")
        calls = asyncio.run(CallLinker().link(self.nodes))
        eff_parent, _eff_children, analysis_ids = build_effective_tree(self.nodes)
        callees_by_node: dict[str, set[str]] = {}
        for edge in calls:
            callees_by_node.setdefault(edge.caller_id, set()).add(edge.callee_id)
        by_id = {node.id: node for node in self.nodes}
        contexts = {
            node_id: AnalysisContext(
                id=node_id,
                signature=by_id[node_id].signature,
                file_path=by_id[node_id].file_path,
                start_line=by_id[node_id].start_line,
                end_line=by_id[node_id].end_line,
                callees=tuple(sorted(callees_by_node.get(node_id, ()))),
            )
            for node_id in analysis_ids
        }
        structural = [(parent, child) for child, parent in eff_parent.items() if parent]
        plan = plan_summary_dependencies(contexts, structural)
        rounds = bottom_up_rounds(plan.schedule_edges, analysis_ids)
        round_of = {
            node_id: index
            for index, round_nodes in enumerate(rounds)
            for node_id in round_nodes
        }

        rows: list[dict[str, object]] = []
        violations: list[dict[str, object]] = []
        for edge in calls:
            owner = edge.caller_id
            seen: set[str] = set()
            while owner in eff_parent and eff_parent.get(owner) and owner not in seen:
                seen.add(owner)
                owner = eff_parent[owner]
            row = {
                "scope": edge.scope,
                "call": edge.caller_id,
                "owner": owner,
                "callee": edge.callee_id,
                "callee_round": round_of.get(edge.callee_id),
                "call_round": round_of.get(edge.caller_id),
                "owner_round": round_of.get(owner),
            }
            rows.append(row)
            if edge.scope == "internal" and not (
                isinstance(row["callee_round"], int)
                and isinstance(row["call_round"], int)
                and isinstance(row["owner_round"], int)
                and row["callee_round"] < row["call_round"] < row["owner_round"]
            ):
                # Cyclic calls intentionally have no callee-summary dependency.
                same_cycle = (
                    edge.caller_id,
                    edge.callee_id,
                ) in plan.suppressed_call_edges
                if not same_cycle:
                    violations.append(row)
        if violations:
            raise ValueError(
                "DBMS callee-summary ordering violation: "
                + json.dumps(violations[:10], ensure_ascii=False)
            )
        return {
            "code_nodes": len(self.nodes),
            "analysis_targets": len(analysis_ids),
            "calls": len(calls),
            "internal": sum(edge.scope == "internal" for edge in calls),
            "external": sum(edge.scope == "external" for edge in calls),
            "rounds": len(rounds),
            "cycles": len(plan.cycle_nodes),
            "rows": rows,
        }

    def dbms_table_semantics(self) -> dict[str, int]:
        """Measure DDL meaning authority and deterministic table LLM targets."""
        if self.strategy != "dbms":
            raise ValueError("table-semantic preflight currently requires a DBMS corpus")
        tables, columns = [], []
        for path in sorted((self.data_dir / "ddl").glob("*.sql")):
            parsed = parse_ddl_file(path, db=self.data_dir.name)
            tables += parsed.tables
            columns += parsed.columns
        edges = _link(self.nodes, tables, self.parent_edges, self.cache)
        by_id = {node.id: node for node in self.nodes}
        direct_items = {(edge.source_id, edge.target_id) for edge in edges}
        inherited_items: set[tuple[str, str]] = set()
        inherited_routines: set[str] = set()
        for edge in edges:
            owner = by_id.get(edge.source_id)
            routine = _owning_routine(owner, by_id) if owner is not None else None
            if routine is not None and routine.id != edge.source_id:
                inherited_items.add((routine.id, edge.target_id))
                inherited_routines.add(routine.id)
        direct_operations: dict[str, list[dict]] = {}
        for edge in edges:
            direct_operations.setdefault(edge.target_id, []).append({})
        targets = _table_target_ids(tables, direct_operations)
        return {
            "tables": len(tables),
            "columns": len(columns),
            "accessed": len(targets),
            "unaccessed": len(tables) - len(targets),
            "table_descriptions": sum(bool(table.description) for table in tables),
            "table_comments": sum(bool(table.comment) for table in tables),
            "column_descriptions": sum(bool(column.description) for column in columns),
            "column_comments": sum(bool(column.comment) for column in columns),
            "direct_insight_items": len(direct_items),
            "direct_insight_nodes": len({source_id for source_id, _ in direct_items}),
            "inherited_duplicate_items": len(inherited_items - direct_items),
            "inherited_routines": len(inherited_routines),
        }

def _measure(data_dir: Path) -> None:
    corpus = _Corpus(data_dir)
    jump_summary = corpus.static_jump_summary()
    print("=" * 72)
    print(
        "[static-jump] routines "
        f"{jump_summary['routines']} · resolved GOTO {jump_summary['resolved_gotos']} · "
        f"target LABEL {jump_summary['target_labels']} · missing/duplicate 0"
    )
    evidence = corpus.evidence()
    roles, roles_direct = Counter(), Counter()
    empty_routines = []
    nested_without_direct: list[tuple[str, str]] = []
    for routine_id, rows in evidence.items():
        if not rows:
            empty_routines.append(routine_id)
        for row_key, row in rows.items():
            roles[row["role"]] += 1
            if row.get("direct"):
                roles_direct[row["role"]] += 1
            elif row.get("nested_control"):
                nested_without_direct.append((routine_id, row_key))
    total = sum(roles.values())
    direct = sum(roles_direct.values())
    print(f"corpus: {data_dir}  strategy={corpus.strategy}  루틴 {len(evidence)}")
    print(f"[의무] {total} 행 · 효과 증거 보유 {direct} "
          f"({direct / total * 100:.0f}%)" if total else "[의무] 0 행")
    for role in sorted(roles):
        print(f"    {role:<11} {roles_direct[role]:>5}/{roles[role]}")
    print(
        f"[nested-control] direct 없는 중첩 제어 행 {len(nested_without_direct)}"
    )
    for routine_id, row_key in nested_without_direct:
        print(f"    {routine_id} {row_key}")
    if empty_routines:
        # 의무 0 인 루틴은 규칙이 한 건도 안 생긴다는 뜻이다 — 조용히 넘기지 않는다.
        print(f"[경고] 의무 0 루틴 {len(empty_routines)}개:")
        for routine_id in empty_routines:
            print(f"    {routine_id}")
    if corpus.strategy == "dbms":
        table_semantics = corpus.dbms_table_semantics()
        print(
            f"[table-semantics] DDL table {table_semantics['tables']} · column "
            f"{table_semantics['columns']} · accessed LLM target "
            f"{table_semantics['accessed']} · unaccessed skip "
            f"{table_semantics['unaccessed']}"
        )
        print(
            "    authoritative description "
            f"table {table_semantics['table_descriptions']} / "
            f"column {table_semantics['column_descriptions']} · raw comment preserved "
            f"table {table_semantics['table_comments']} / "
            f"column {table_semantics['column_comments']}"
        )
        print(
            "    table-insight direct items "
            f"{table_semantics['direct_insight_items']} / owner nodes "
            f"{table_semantics['direct_insight_nodes']} · inherited duplicate items "
            f"{table_semantics['inherited_duplicate_items']} / routines "
            f"{table_semantics['inherited_routines']}"
        )
        summary = corpus.dbms_summary_dependencies()
        print(
            f"[callee-summary] CodeNode {summary['code_nodes']} · 분석대상 "
            f"{summary['analysis_targets']} · CALLS {summary['calls']} "
            f"(internal {summary['internal']} / external {summary['external']}) · "
            f"rounds {summary['rounds']} · cycle nodes {summary['cycles']}"
        )
        for row in summary["rows"]:
            if row["scope"] == "internal":
                print(
                    "    internal "
                    f"target {row['callee_round']} → CALL {row['call_round']} "
                    f"→ owner {row['owner_round']}  {row['call']}"
                )
def _resolved_rows(payload: dict) -> dict[str, dict]:
    """Dereference local C/E keys so batches can be compared by actual evidence."""
    conditions = payload.get("conditions") or {}
    effects = payload.get("effects") or {}
    resolved: dict[str, dict] = {}
    for key, raw in (payload.get("rows") or {}).items():
        row = dict(raw)
        row["when_evidence"] = [
            {
                "holds": not str(reference).startswith("!"),
                "source": conditions.get(str(reference).lstrip("!")),
            }
            for reference in row.pop("when", ())
        ]
        row["direct_evidence"] = [
            effects.get(reference) for reference in row.pop("direct", ())
        ]
        resolved[key] = row
    return resolved


def _stored_rows(run_dir: Path) -> dict[str, dict]:
    """저장 run에서 ``target_id → resolved rows``를 읽는다.

    Current runs own a dedicated deterministic Rule artifact.  Prompt parsing remains
    only for historical provider-backed runs.
    """
    audit_records = load_rule_audit_records(run_dir)
    if audit_records:
        return {
            target_id: _resolved_rows(record["evidence"])
            for target_id, record in audit_records.items()
        }
    out: dict[str, dict] = {}
    prompt_paths = {
        *sorted((run_dir / "llm-prompts").glob("code_rules_examples*.jsonl")),
        *sorted((run_dir / "llm-prompts").glob("code_gate_rules*.jsonl")),
    }
    for path in sorted(prompt_paths):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                for owner_id, payload in _stored_rule_packets(record):
                    out.setdefault(owner_id, {}).update(_resolved_rows(payload))
    return out


def _stored_rule_packets(record: dict) -> list[tuple[str, dict]]:
    """Return every batch block with its Rule owner, not the trace envelope target."""
    text = str(record.get("user_content") or "")
    headings = list(re.finditer(
        r"(?m)^### \[RULE_TARGET [^\]\r\n]+\]\r?$", text,
    ))
    if not headings:
        payload = _embedded_evidence(text)
        target = str(record.get("target_id") or "").strip()
        return [(target, payload)] if target and payload is not None else []
    packets: list[tuple[str, dict]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.start():end]
        owner_match = re.search(r"(?m)^owner_id:\s*(\S.*?)\s*$", block)
        target_match = re.search(r"(?m)^target_id:\s*(\S.*?)\s*$", block)
        owner_id = str(
            owner_match.group(1) if owner_match else
            target_match.group(1) if target_match else ""
        ).strip()
        payload = _embedded_evidence(block)
        if owner_id and payload is not None:
            packets.append((owner_id, payload))
    return packets


def _embedded_evidence(text: str) -> dict | None:
    """프롬프트 본문에 박힌 evidence JSON 을 균형 파싱으로 꺼낸다."""
    start = text.find('{"conditions"')
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])
    return None


def _diff(run_dir: Path, data_dir: Path) -> None:
    stored = _stored_rows(run_dir)
    computed = {
        target: _resolved_rows(packet)
        for target, packet in _Corpus(data_dir).evidence_packets().items()
    }
    print("=" * 72)
    print(f"저장 run : {run_dir.name}  target {len(stored)} · 행 "
          f"{sum(len(v) for v in stored.values())}")
    print(f"현재 코드: {data_dir}  target {len(computed)} · 행 "
          f"{sum(len(v) for v in computed.values())}")
    diffs, examples = Counter(), []

    def note(kind: str, text: str) -> None:
        diffs[kind] += 1
        if len(examples) < 40:
            examples.append(f"[{kind}] {text}")

    for target, rows in stored.items():
        current = computed.get(target)
        if current is None:
            note("target 없음", target)
            continue
        for key, row in rows.items():
            fresh = current.get(key)
            if fresh is None:
                note("행 사라짐", f"{target} {key} role={row.get('role')}")
                continue
            changed = [
                field for field in sorted(set(row) | set(fresh))
                if row.get(field) != fresh.get(field)
            ]
            if changed:
                note(
                    "행 증거 변경",
                    f"{target} {key}: fields={changed} "
                    f"stored={row!r} current={fresh!r}",
                )
        for key in current:
            if key not in rows:
                note("행 신설", f"{target} {key} role={current[key].get('role')}")
    for target in computed:
        if target not in stored:
            note("target 신설", target)
    print()
    print("차이:", dict(diffs) or "없음 (0)")
    for line in examples:
        print("   ", line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["measure", "diff"], nargs="?", default="measure")
    parser.add_argument("run_dir", nargs="?", help="diff 모드의 저장 run 폴더")
    parser.add_argument("--data-dir", help="코퍼스 루트 (기본: ROBO_DATA_DIR)")
    parser.add_argument("--corpus", help="diff 모드에서 그 run 이 쓴 코퍼스 루트")
    args = parser.parse_args()

    default_dir = SETTINGS.analysis_data.dir
    if args.mode == "measure":
        _measure(Path(args.data_dir or default_dir).resolve())
        return
    if not args.run_dir:
        parser.error("diff 모드는 run 폴더가 필요하다")
    _diff(
        Path(args.run_dir).resolve(),
        Path(args.corpus or args.data_dir or default_dir).resolve(),
    )


if __name__ == "__main__":
    main()
