"""run 판정 도구 — 헌법 IX 3층 중 1·2층(자동 검사 가능한 축)의 상설 소유자.

절차 정본은 `docs/handovers/analysis-performance-validation-canonical/04-검증방법.md`.
새 검증 축이 생기면 일회용 스크립트를 쓰지 말고 여기에 추가해 매 run 상시로 돌게 한다.

사용:
    .venv\\Scripts\\python.exe scripts\\judge_run.py                  # 최신 run
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir>
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --baseline <run_dir>
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --graph  # 그래프 대조 축 포함
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --graph --gold <gold_dir>

그래프 축은 Neo4j 를 읽는다(읽기는 자유 — 01 §3). 대상 DB 는 `ROBO_NEO4J_DATABASE`
(framework=test, DBMS=neo4j — 04 §4.6). run 이 쓴 DB 와 다르면 결과가 무의미하므로
manifest 의 `neo4j_database` 와 다르면 경고한다.

판정 축 (골드셋 없이 결정론 — 04 §3, 08 §20.0)
  [A] 회계    : 분기표 행 수(obligation) — 결정론 예측치와 일치해야 구조가 안 깨진 것
  [B] findings: 품질 격리 상태별 집계 — 정확도 축
  [C] nl 품질 : 참조번호 누출·공허 서술·상한 절단 — record 응답 전수
  [D] 비교    : baseline run 과 공통 행의 cond 채점 대조 — 경로 동일/변경 분리
  [E] RETURN  : 원문 occurrence(독립 정답지) ↔ AST ↔ 행 증거 (04 §3.3)
  [F] CALL    : AST FUNCTION_CALL(독립 정답지) ↔ 행 증거 call 효과 ↔ 그래프 CALLS (04 §3.1)
  [G] R/W     : 결정론 링커 reads/writes ↔ 그래프 READS/WRITES, callee 오귀속 (04 §3.2)
  [H] EXAMPLE : 채택 RULE ↔ EXAMPLE 전수 존재·파생 (04 §3.5)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.quality_gates.nl_reference_safety import (
    MIN_REFERENCE_LEAK,
    reference_leak_count,
)
from pipeline.quality_gates.rule_audit_artifact import load_rule_audit_records
from pipeline.quality_gates.rule_prompt_evidence import render_row_condition
from pipeline.steps.step2_load_ast.load_ast_step import ast_json_paths

RUN_DIR_NAME = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")
VAGUE = re.compile(r"해당 케이스를 처리한다|다음 단계로 진행한다$|처리하고 종료한다$")
# Historical provider-backed runs used a 600-character Rule narrative leaf.  Current
# deterministic Rules have ``nl=""`` and are audited through ``rule-structures``.
LEGACY_RULE_NL_MAX_LENGTH = 600
NL_LIMIT = LEGACY_RULE_NL_MAX_LENGTH
STRING_LITERAL = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\'\'|\\.|[^\'\\])*\'|`(?:``|[^`])*`'
)


def _reference_leak_count(row: dict) -> int:
    """Count only evidence IDs that the current row could actually echo.

    A bare ``C3``/``E5`` can be a real legacy status code.  Treating every such token as
    an internal-reference leak made the checker accuse correct business text.  The
    evidence row already declares the only private IDs exposed to the model, so the
    measurement must intersect with that exact set.
    """
    return reference_leak_count(str(row.get("nl") or ""), row)


def _delivered_nl_quality(
    graph_rows: list[dict], evidence_rows: dict[tuple[str, str], dict],
) -> Counter:
    """Measure consumer-visible graph narratives against the same row evidence."""
    quality = Counter()
    for graph_row in graph_rows:
        key = (
            str(graph_row.get("target_id") or ""),
            f"R{int(graph_row.get('anchor_line') or 0)}",
        )
        evidence = evidence_rows.get(key)
        if evidence is None:
            quality["unmatched"] += 1
            continue
        narrative = str(graph_row.get("nl") or "").strip()
        if not narrative:
            continue
        quality["n"] += 1
        delivered = {**evidence, "nl": narrative}
        if _reference_leak_count(delivered) >= MIN_REFERENCE_LEAK:
            quality["leak"] += 1
        if VAGUE.search(narrative):
            quality["vague"] += 1
        # Frame projections deliberately preserve complete meaning beyond the old
        # 600-character model wire limit.  Only landing exactly on that legacy limit
        # is a truncation signal; a longer projection is not evidence of cutting.
        if len(narrative) == NL_LIMIT:
            quality["at_limit"] += 1
    return quality


def _graph_rule_structure_delta(
    graph_rows: list[dict], expected_rows: dict[tuple[str, str], dict],
) -> dict[str, object]:
    """Compare consumer-visible Rule structure with the immutable run artifact."""
    actual = {
        (
            str(row.get("target_id") or ""),
            f"R{int(row.get('anchor_line') or 0)}",
        ): row
        for row in graph_rows
    }
    missing = sorted(expected_rows.keys() - actual.keys())
    extra = sorted(actual.keys() - expected_rows.keys())
    mismatches: list[tuple[str, str]] = []
    for key in sorted(expected_rows.keys() & actual.keys()):
        expected = expected_rows[key]
        delivered = actual[key]
        if (
            (delivered.get("cond") or None) != (expected.get("cond") or None)
            or list(delivered.get("effects") or ()) != list(expected.get("then") or ())
            or str(delivered.get("nl") or "") != str(expected.get("nl") or "")
        ):
            mismatches.append(key)
    return {
        "expected": len(expected_rows),
        "actual": len(actual),
        "missing": missing,
        "extra": extra,
        "mismatches": mismatches,
    }


def latest_run_dir(root: Path) -> Path:
    runs = sorted(
        entry for entry in root.iterdir()
        if entry.is_dir() and RUN_DIR_NAME.match(entry.name)
    ) if root.is_dir() else []
    if not runs:
        raise SystemExit(f"run 폴더가 없다: {root}")
    return runs[-1]


def _evidence_rows(content: str) -> dict[str, dict]:
    """user_content 안의 모든 증거 블록에서 R키 → 행 사실을 모은다.

    framework 는 record 당 블록 1개, DBMS 배치는 노드마다 1개다. R키는 절대 줄번호라
    한 record(한 루틴) 안에서 유일하다.
    """
    decoder = json.JSONDecoder()
    rows: dict[str, dict] = {}
    cursor = 0
    while True:
        head = content.find('{"conditions"', cursor)
        if head < 0:
            return rows
        try:
            evidence, consumed = decoder.raw_decode(content[head:])
        except json.JSONDecodeError:
            cursor = head + 1
            continue
        cursor = head + consumed
        for row_key, row in (evidence.get("rows") or {}).items():
            rows[row_key] = row


def _response_rules(response: object) -> dict[str, dict]:
    """framework(rules 평면)·DBMS 배치(nodes.*.rules) 응답에서 R키 → rule 을 모은다."""
    response = _response_payload(response)
    if not isinstance(response, dict):
        return {}
    rules: dict[str, dict] = {}
    for row_key, rule in (response.get("rules") or {}).items():
        if isinstance(rule, dict):
            rules[row_key] = rule
    for node in (response.get("nodes") or {}).values():
        if isinstance(node, dict):
            for row_key, rule in (node.get("rules") or {}).items():
                if isinstance(rule, dict):
                    rules[row_key] = rule
    return rules


def _response_payload(response: object) -> dict:
    """저장 response 봉투에서 구조화 payload만 꺼낸다."""
    if isinstance(response, dict):
        response = response.get("parsed") or response.get("raw") or response
    if isinstance(response, dict) and "content" in response:
        try:
            response = json.loads(response["content"])
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(response, dict):
        return {}
    return response


_RULE_TARGET_HEAD = re.compile(
    r"^### \[RULE_TARGET ([^]]+)\]\ntarget_id: ([^\n]+)", re.MULTILINE,
)


def _rule_target_blocks(content: str) -> list[tuple[str, str, dict[str, dict]]]:
    """DBMS user_content의 node_key·실제 target_id·행 증거를 보존한다."""
    matches = list(_RULE_TARGET_HEAD.finditer(content))
    blocks: list[tuple[str, str, dict[str, dict]]] = []
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        evidence = _evidence_rows(content[match.start():stop])
        blocks.append((match.group(1), match.group(2).strip(), evidence))
    return blocks


def load_rule_obligations(run_dir: Path) -> set[tuple[str, str]]:
    """저장 prompt가 요구한 모든 RULE 행의 실제 신원 집합."""
    audit_records = load_rule_audit_records(run_dir)
    if audit_records:
        return {
            (target_id, str(row_key))
            for target_id, record in audit_records.items()
            for row_key in (record["evidence"].get("rows") or {})
        }
    obligations: set[tuple[str, str]] = set()
    for record_path in sorted((run_dir / "llm-prompts").glob("*rules*.jsonl")):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                content = record.get("user_content") or ""
                blocks = _rule_target_blocks(content)
                if blocks:
                    for _node_key, target_id, evidence_rows in blocks:
                        obligations.update(
                            (target_id, row_key) for row_key in evidence_rows
                        )
                else:
                    target_id = str(record.get("target_id") or "unknown")
                    obligations.update(
                        (target_id, row_key)
                        for row_key in _evidence_rows(content)
                    )
    return obligations


def load_rule_rows(run_dir: Path) -> dict[tuple[str, str], dict]:
    """record 에서 (target_id, R키) → {cond, then, nl, when, direct} 를 전수 수집."""
    audit_records = load_rule_audit_records(run_dir)
    if audit_records:
        rows: dict[tuple[str, str], dict] = {}
        for target_id, record in audit_records.items():
            evidence = record["evidence"]
            conditions = evidence.get("conditions") or {}
            evidence_rows = evidence.get("rows") or {}
            rules = {
                f"R{int(rule['anchor_line'])}": rule for rule in record["rules"]
            }
            for row_key, evidence_row in evidence_rows.items():
                rule = rules[row_key]
                rows[(target_id, str(row_key))] = {
                    "cond": rule.get("cond"),
                    "then": list(rule.get("then") or ()),
                    "nl": str(rule.get("nl") or ""),
                    "when": list(evidence_row.get("when") or ()),
                    "direct": list(evidence_row.get("direct") or ()),
                    "expected_cond": render_row_condition(evidence_row, conditions),
                }
        return rows
    rows: dict[tuple[str, str], dict] = {}
    for record_path in sorted((run_dir / "llm-prompts").glob("*rules*.jsonl")):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                content = record.get("user_content") or ""
                blocks = _rule_target_blocks(content)
                payload = _response_payload(record.get("response"))
                if blocks:
                    for node_key, target_id, evidence_rows in blocks:
                        for row_key, evidence in evidence_rows.items():
                            rows[(target_id, row_key)] = {
                                "cond": list(evidence.get("when") or []),
                                "then": list(evidence.get("direct") or []),
                                "nl": "",
                                "when": evidence.get("when") or [],
                                "direct": evidence.get("direct") or [],
                            }
                        node = (payload.get("nodes") or {}).get(node_key) or {}
                        for row_key, rule in (node.get("rules") or {}).items():
                            row = evidence_rows.get(row_key)
                            if row is None or not isinstance(rule, dict):
                                continue
                            rows[(target_id, row_key)] = {
                                "cond": rule.get("cond") or [],
                                "then": rule.get("then") or [],
                                "nl": (rule.get("nl") or "").strip(),
                                "when": row.get("when") or [],
                                "direct": row.get("direct") or [],
                            }
                    continue
                evidence_rows = _evidence_rows(content)
                if not evidence_rows:
                    continue
                target_id = str(record.get("target_id") or "unknown")
                for row_key, evidence in evidence_rows.items():
                    rows[(target_id, row_key)] = {
                        "cond": list(evidence.get("when") or []),
                        "then": list(evidence.get("direct") or []),
                        "nl": "",
                        "when": evidence.get("when") or [],
                        "direct": evidence.get("direct") or [],
                    }
                for row_key, rule in _response_rules(record.get("response")).items():
                    row = evidence_rows.get(row_key)
                    if row is None:
                        continue
                    rows[(target_id, row_key)] = {
                        "cond": rule.get("cond") or [],
                        "then": rule.get("then") or [],
                        "nl": (rule.get("nl") or "").strip(),
                        "when": row.get("when") or [],
                        "direct": row.get("direct") or [],
                    }
    return rows


def _ast_facts(corpus_root: Path) -> dict[str, dict]:
    """AST JSON 에서 루틴별 구조 사실을 독립 수집 — analyzer 소비 경로를 거치지 않는다.

    반환: {routine_name: {"returns": [line...], "calls": {name...}, "file": str}}
    """
    facts: dict[str, dict] = {}
    routine_types = {"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"}

    def walk(node: dict, current: dict | None, file_name: str) -> None:
        ntype = str(node.get("type") or "")
        if ntype in routine_types and node.get("name"):
            current = facts.setdefault(str(node["name"]), {
                "returns": [], "calls": set(), "file": file_name,
            })
        if current is not None:
            if ntype in {"RETURN", "THROW", "RAISE"}:
                line = node.get("startLine") or node.get("start_line")
                if isinstance(line, int):
                    current["returns"].append(line)
            elif ntype in {"FUNCTION_CALL", "CALL"} and node.get("name"):
                # 대소문자는 호출 동일성의 근거가 아니다 — 운영 채점기(rule_grading)도
                # casefold 로 비교한다. 여기서 구별하면 매크로 표기 차이가 전부 오탐이 된다.
                current["calls"].add(str(node["name"]).casefold())
        for child in node.get("children") or ():
            if isinstance(child, dict):
                walk(child, current, file_name)

    for path in ast_json_paths(corpus_root):
        try:
            tree = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        roots = tree if isinstance(tree, list) else [tree]
        for root in roots:
            if isinstance(root, dict):
                walk(root, None, path.name)
    return facts


def _source_return_lines(source_root: Path) -> dict[str, int]:
    """원문에서 return/throw/raise occurrence 를 파일 단위로 독립 계수한다.

    04 §3.3 이 요구하는 '독립 정답지'다. 주석·문자열은 제외한다 — analyzer 를 거치지
    않아야 AST 누락을 잡을 수 있으므로 여기서만 텍스트를 본다.
    """
    keyword = re.compile(
        r"(?<![A-Za-z0-9_])(return|throw|raise)(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    line_comment = re.compile(r"//.*$|--.*$|#.*$")
    counts: dict[str, int] = {}
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {
            ".c", ".h", ".java", ".py", ".sql", ".pls", ".pkb", ".pks",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        total = 0
        for line in text.splitlines():
            active = STRING_LITERAL.sub("", line_comment.sub("", line))
            hits = keyword.findall(active)
            if path.suffix.lower() in {".sql", ".pls", ".pkb", ".pks"}:
                # PL/SQL 함수 선언의 `RETURN TYPE`은 실행문이 아니다. SQL 계열에서
                # statement는 세미콜론으로 닫혀야 하므로 헤더 토큰을 독립 정답지에서 제외한다.
                hits = hits if ";" in active else []
            total += len(hits)
        counts[path.name] = total
    return counts


def _evidence_effects(run_dir: Path) -> tuple[Counter, dict[str, set[str]]]:
    """행 증거의 효과를 집계한다 — op 별 개수와 target별 호출 대상 집합."""
    ops: Counter = Counter()
    calls_by_target: dict[str, set[str]] = defaultdict(set)
    audit_records = load_rule_audit_records(run_dir)
    if audit_records:
        for target_id, record in audit_records.items():
            evidence = record["evidence"]
            effects = evidence.get("effects") or {}
            routine = _routine_from_target_id(target_id)
            for row in (evidence.get("rows") or {}).values():
                for reference in row.get("direct") or ():
                    effect = effects.get(str(reference)) or {}
                    op = str(effect.get("op") or "")
                    ops[op] += 1
                    if op == "call" and effect.get("target"):
                        calls_by_target[routine].add(
                            str(effect["target"]).casefold()
                        )
        return ops, calls_by_target
    for record_path in sorted((run_dir / "llm-prompts").glob("*rules*.jsonl")):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                content = record.get("user_content") or ""
                matches = list(_RULE_TARGET_HEAD.finditer(content))
                segments: list[tuple[str, str]] = []
                if matches:
                    for index, match in enumerate(matches):
                        stop = (
                            matches[index + 1].start()
                            if index + 1 < len(matches) else len(content)
                        )
                        segments.append((
                            _routine_from_target_id(match.group(2).strip()),
                            content[match.start():stop],
                        ))
                else:
                    segments.append((
                        _routine_from_target_id(
                            str(record.get("target_id") or "unknown")
                        ),
                        content,
                    ))
                for routine, segment in segments:
                    decoder = json.JSONDecoder()
                    cursor = 0
                    while True:
                        head = segment.find('{"conditions"', cursor)
                        if head < 0:
                            break
                        try:
                            evidence, consumed = decoder.raw_decode(segment[head:])
                        except json.JSONDecodeError:
                            cursor = head + 1
                            continue
                        cursor = head + consumed
                        for effect in (evidence.get("effects") or {}).values():
                            op = str(effect.get("op") or "")
                            ops[op] += 1
                            if op == "call" and effect.get("target"):
                                calls_by_target[routine].add(
                                    str(effect["target"]).casefold()
                                )
    return ops, calls_by_target


def _routine_from_target_id(target_id: str) -> str:
    """CodeNode ID에서 소유 루틴의 AST name을 복원한다."""
    tail = target_id.split(":", 2)[-1]
    if "@" in tail:
        return tail.split("@", 1)[0]
    return tail.split(":", 1)[0]


def _gold_source_file(
    value: str, source_files: Mapping[str, str] | None = None,
) -> str:
    normalized = str(value).replace("\\", "/")
    normalized = normalized.removeprefix("source/").casefold()
    if source_files is None or normalized in source_files:
        return normalized
    return source_files.get(PureWindowsPath(normalized).name.casefold(), normalized)


def _gold_routine_id(
    value: str, source_files: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    raw = str(value)
    tail = raw.split(":", 1)[1] if ":" in raw else raw
    file_name, name_line = tail.rsplit(":", 1)
    name = name_line.rsplit("@", 1)[0].rsplit(".", 1)[-1].strip('"').casefold()
    return _gold_source_file(file_name, source_files), name


DDL_TABLE = re.compile(
    r'\bCREATE\s+TABLE\s+(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)\s*\.\s*)?'
    r'(?P<name>"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)',
    re.IGNORECASE,
)


def _ddl_table_names(ddl_dir: Path) -> set[str]:
    """Read only CREATE TABLE identities to scope a broader independent gold set."""
    names: set[str] = set()
    if not ddl_dir.is_dir():
        return names
    for path in sorted(ddl_dir.rglob("*.sql")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        names.update(
            match.group("name").strip('"').casefold()
            for match in DDL_TABLE.finditer(text)
        )
    return names


DDL_BLOCK = re.compile(
    r'\bCREATE\s+TABLE\s+(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)\s*\.\s*)?'
    r'(?P<name>"[^"]+"|[A-Za-z_][A-Za-z0-9_$#]*)\s*\('
    r'(?P<body>.*?)\)\s*;',
    re.IGNORECASE | re.DOTALL,
)
DDL_COLUMN_LINE = re.compile(r'^\s*(?P<name>"(?:""|[^"])+"|[A-Za-z_][A-Za-z0-9_$#]*)\s+', re.MULTILINE)


def _ddl_axes(ddl_dir: Path) -> tuple[set[str], set[tuple[str, str]]]:
    """Independent axes for the exact DDL supplied to this run."""
    tables: set[str] = set()
    columns: set[tuple[str, str]] = set()
    if not ddl_dir.is_dir():
        return tables, columns
    for path in sorted(ddl_dir.rglob("*.sql")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in DDL_BLOCK.finditer(text):
            table = match.group("name").strip('"').casefold()
            tables.add(table)
            for column_match in DDL_COLUMN_LINE.finditer(match.group("body")):
                column = column_match.group("name").strip('"').replace('""', '"').casefold()
                if column.upper() not in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                    columns.add((table, column))
    return tables, columns


def _optional_json(path: Path, default: object) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def load_gold_axes(
    gold_dir: Path, *, table_scope: set[str] | None = None,
    source_root: Path | None = None,
) -> dict[str, set]:
    """Load strict source-derived axes, honoring manifest precedence for R/W."""
    routines_raw = json.loads((gold_dir / "routines.json").read_text(encoding="utf-8-sig"))
    tables_raw = json.loads((gold_dir / "tables.json").read_text(encoding="utf-8-sig"))
    relations = json.loads((gold_dir / "relationships.json").read_text(encoding="utf-8-sig"))
    functions_raw = _optional_json(gold_dir / "functions.json", {})
    functions = functions_raw if isinstance(functions_raw, dict) else {}
    references = _optional_json(gold_dir / "references.json", {})
    init_by = _optional_json(gold_dir / "init_by.json", [])

    source_files: dict[str, str] | None = None
    if source_root is not None and source_root.is_dir():
        relative = [
            path.relative_to(source_root).as_posix().casefold()
            for path in source_root.rglob("*") if path.is_file()
        ]
        by_basename = Counter(PureWindowsPath(path).name.casefold() for path in relative)
        source_files = {path: path for path in relative}
        source_files.update({
            PureWindowsPath(path).name.casefold(): path
            for path in relative
            if by_basename[PureWindowsPath(path).name.casefold()] == 1
        })
    routines = {
        (_gold_source_file(row["file"], source_files), str(row["name"]).strip('"').casefold())
        for row in routines_raw
    }
    routine_by_name = {name: (file_name, name) for file_name, name in routines}
    tables = {
        str(row["name"]).casefold() for row in tables_raw
        if table_scope is None or str(row["name"]).casefold() in table_scope
    }
    columns = {
        (str(row["name"]).casefold(), str(column["name"]).casefold())
        for row in tables_raw
        if table_scope is None or str(row["name"]).casefold() in table_scope
        for column in row.get("columns") or ()
    }
    calls = {
        (_gold_routine_id(row["from"], source_files), _gold_routine_id(row["to"], source_files))
        for row in relations.get("CALLS") or ()
    }

    reads: set = set()
    writes: set = set()
    for name, facts in functions.items():
        caller = routine_by_name.get(str(name).casefold())
        if caller is None:
            continue
        reads.update((caller, str(table).casefold()) for table in facts.get("reads") or ())
        writes.update((caller, str(table).casefold()) for table in facts.get("writes") or ())

    # The historical RWIS gold has two independently read layers. functions.json owns
    # strict business-table occurrences, while routines.json preserves DUAL/system-view
    # reads.  Their union is the complete source axis for normal routines.  For a trigger,
    # routines.json intentionally distinguishes the ON target from a READ; prefer that
    # adjudication instead of misclassifying the trigger subject as queried data.
    for row in routines_raw:
        caller = routine_by_name.get(str(row.get("name") or "").casefold())
        if caller is None:
            continue
        facts = row.get("quality_facts") or {}
        if str(row.get("kind") or "").upper() == "TRIGGER":
            reads = {pair for pair in reads if pair[0] != caller}
            writes = {pair for pair in writes if pair[0] != caller}
        reads.update((caller, str(table).casefold()) for table in facts.get("reads") or ())
        writes.update((caller, str(table).casefold()) for table in facts.get("writes") or ())

    strict_references = {
        (
            routine_by_name[str(name).casefold()],
            PureWindowsPath(str(target["file"])).name.casefold(),
            str(target["name"]).casefold(),
            str(target["kind"]).upper(),
        )
        for name, targets in references.items()
        if str(name).casefold() in routine_by_name
        for target in targets
    }
    strict_init_by = {
        (
            PureWindowsPath(str(row["from"]["file"])).name.casefold(),
            str(row["from"]["name"]).casefold(),
            str(row["from"]["kind"]).upper(),
            PureWindowsPath(str(row["to"]["file"])).name.casefold(),
            str(row["to"]["name"]).casefold(),
            str(row["to"]["kind"]).upper(),
        )
        for row in init_by
    }
    return {
        "ROUTINE": routines,
        "TABLE": tables,
        "COLUMN": columns,
        "CALLS": calls,
        "READS": reads,
        "WRITES": writes,
        "REFERENCES": strict_references,
        "INIT_BY": strict_init_by,
    }


def _actual_source_file(value: str, source_root: Path) -> str:
    path = Path(value).resolve()
    try:
        return path.relative_to(source_root.resolve()).as_posix().casefold()
    except ValueError:
        return PureWindowsPath(str(value)).name.casefold()


async def _graph_gold_axes(source_root: Path) -> dict[str, set]:
    """Read the graph axes comparable to the strict independent shop gold."""
    from graph.neo4j_client import Neo4jClient

    client = Neo4jClient()
    try:
        async def rows(query: str) -> list[dict]:
            return await client.run_read_query(query, {})

        routine_rows = await rows(
            "MATCH (n) WHERE n.file_path IS NOT NULL AND "
            "any(label IN labels(n) WHERE label IN ['FUNCTION','PROCEDURE','METHOD','TRIGGER']) "
            "RETURN n.file_path AS file, n.name AS name"
        )
        table_rows = await rows("MATCH (n:TABLE) RETURN n.name AS name")
        column_rows = await rows(
            "MATCH (t:TABLE)-[:HAS_COLUMN]->(c:COLUMN) "
            "RETURN t.name AS table_name, c.name AS column_name"
        )
        call_rows = await rows(
            "MATCH (routine)-[:PARENT_OF*0..]->(a)-[:CALLS]->(b) "
            "WHERE routine.file_path IS NOT NULL AND b.file_path IS NOT NULL AND "
            "any(label IN labels(routine) WHERE "
            "label IN ['FUNCTION','PROCEDURE','METHOD','TRIGGER']) "
            "RETURN routine.file_path AS afile, routine.name AS aname, "
            "b.file_path AS bfile, b.name AS bname"
        )
        rw_rows = {
            relation: await rows(
                f"MATCH (routine)-[:PARENT_OF*0..]->(a)-[:{relation}]->(b) "
                "WHERE any(label IN labels(routine) WHERE "
                "label IN ['FUNCTION','PROCEDURE','METHOD','TRIGGER']) "
                "AND (b:TABLE OR b:OBJECT_REFERENCE) "
                "RETURN routine.file_path AS file, routine.name AS name, "
                "b.name AS table_name"
            )
            for relation in ("READS", "WRITES")
        }
        reference_rows = await rows(
            "MATCH (a)-[:REFERENCES]->(b) WHERE a.file_path IS NOT NULL AND b.file_path IS NOT NULL AND "
            "any(label IN labels(a) WHERE label IN ['FUNCTION','PROCEDURE','METHOD','TRIGGER']) "
            "RETURN a.file_path AS afile, a.name AS aname, b.file_path AS bfile, "
            "b.name AS bname, labels(b) AS labels"
        )
        init_rows = await rows(
            "MATCH (a)-[:INIT_BY]->(b) WHERE a.file_path IS NOT NULL AND b.file_path IS NOT NULL "
            "RETURN a.file_path AS afile, a.name AS aname, labels(a) AS alabels, "
            "b.file_path AS bfile, b.name AS bname, labels(b) AS blabels"
        )
    finally:
        await client.close()

    routine_key = lambda file_name, name: (
        _actual_source_file(file_name, source_root), str(name).strip('"').casefold(),
    )
    symbol_kinds = ("DEFINE", "MEMBER", "GLOBAL_VARIABLE", "CONSTANT_FIELD")
    kind = lambda labels: next((label for label in symbol_kinds if label in labels), "")
    return {
        "ROUTINE": {routine_key(row["file"], row["name"]) for row in routine_rows},
        "TABLE": {str(row["name"]).casefold() for row in table_rows},
        "COLUMN": {
            (str(row["table_name"]).casefold(), str(row["column_name"]).casefold())
            for row in column_rows
        },
        "CALLS": {
            (routine_key(row["afile"], row["aname"]), routine_key(row["bfile"], row["bname"]))
            for row in call_rows
        },
        **{
            relation: {
                (routine_key(row["file"], row["name"]), str(row["table_name"]).casefold())
                for row in values
            }
            for relation, values in rw_rows.items()
        },
        "REFERENCES": {
            (
                routine_key(row["afile"], row["aname"]),
                PureWindowsPath(str(row["bfile"])).name.casefold(),
                str(row["bname"]).casefold(),
                kind(row["labels"]),
            )
            for row in reference_rows if kind(row["labels"])
        },
        "INIT_BY": {
            (
                PureWindowsPath(str(row["afile"])).name.casefold(),
                str(row["aname"]).casefold(), kind(row["alabels"]),
                PureWindowsPath(str(row["bfile"])).name.casefold(),
                str(row["bname"]).casefold(), kind(row["blabels"]),
            )
            for row in init_rows if kind(row["alabels"]) and kind(row["blabels"])
        },
    }


def _print_gold_comparison(gold: dict[str, set], actual: dict[str, set]) -> None:
    print("\n[I] 독립 gold 집합 대조")
    for axis in ("ROUTINE", "TABLE", "COLUMN", "CALLS", "READS", "WRITES", "REFERENCES", "INIT_BY"):
        missing = gold[axis] - actual[axis]
        extra = actual[axis] - gold[axis]
        print(
            f"    {axis:<10} gold {len(gold[axis]):>4} · graph {len(actual[axis]):>4} · "
            f"누락 {len(missing):>3} · 추가 {len(extra):>3}"
        )
        if missing:
            print(f"      missing sample: {sorted(missing, key=repr)[:3]}")
        if extra:
            print(f"      extra sample: {sorted(extra, key=repr)[:3]}")


async def _graph_axes(database: str | None) -> dict[str, object]:
    """그래프에서 CALL/RW/EXAMPLE 축의 실측값을 읽는다 (읽기 전용)."""
    from graph.neo4j_client import Neo4jClient

    client = Neo4jClient()
    try:
        async def one(query: str) -> list[dict]:
            return await client.run_read_query(query, {})

        counts = await one(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n "
            "ORDER BY n DESC LIMIT 12"
        )
        rels = await one(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n "
            "ORDER BY n DESC LIMIT 12"
        )
        # cond 가 빈 규칙(무조건 실행 행)과 direct outcome이 없는 nested-control 행은 예시를
        # 만들지 않는 것이 계약이다(example_synthesis). 둘을 위반으로 세면 진짜 신호가 묻힌다.
        rule_example = await one(
            "MATCH (r:RULE) "
            "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE) "
            "WITH r, count(e) AS examples, "
            "     (r.cond IS NULL OR r.cond = '') AS unconditional, "
            "     (r.then IS NOT NULL AND size(r.then) > 0) AS has_outcome "
            "RETURN count(r) AS rules, "
            "       sum(CASE WHEN unconditional THEN 1 ELSE 0 END) AS unconditional_rules, "
            "       sum(CASE WHEN NOT unconditional AND NOT has_outcome THEN 1 ELSE 0 END) "
            "         AS conditional_without_outcome, "
            "       sum(CASE WHEN NOT unconditional AND has_outcome AND examples = 0 THEN 1 ELSE 0 END) "
            "         AS conditional_without_example, "
            "       sum(examples) AS example_links"
        )
        orphan_example = await one(
            "MATCH (e:EXAMPLE) WHERE NOT ()-[:HAS_EXAMPLE]->(e) "
            "RETURN count(e) AS orphans"
        )
        rule_rows = await one(
            "MATCH (c)-[:HAS_RULE]->(r:RULE) "
            "RETURN c.id AS target_id, r.anchor_line AS anchor_line, "
            "r.cond AS cond, r.then AS effects, r.nl AS nl"
        )
        return {
            "database": getattr(client, "_database", None),
            "labels": counts,
            "relations": rels,
            "rule_example": rule_example[0] if rule_example else {},
            "orphan_example": orphan_example[0] if orphan_example else {},
            "rule_rows": rule_rows,
        }
    finally:
        await client.close()


def judge(
    run_dir: Path,
    baseline_dir: Path | None,
    with_graph: bool = False,
    gold_dir: Path | None = None,
) -> None:
    print("=" * 72)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"run: {manifest['run_id']}  corpus: {Path(manifest['corpus_root']).name}")

    # [A] 회계
    obligation = len(load_rule_obligations(run_dir))
    print(f"\n[A] obligation(분기표 행): {obligation}")

    # [B] findings
    findings = Counter()
    findings_path = run_dir / "analysis-findings.jsonl"
    if findings_path.exists():
        with findings_path.open(encoding="utf-8") as fh:
            for line in fh:
                findings[json.loads(line).get("status")] += 1
    print(f"[B] findings: {dict(findings)} · 합계 {sum(findings.values())}")

    # [C] nl 품질
    rows = load_rule_rows(run_dir)
    quality = Counter()
    for row in rows.values():
        nl = row["nl"]
        if not nl:
            continue
        quality["n"] += 1
        if _reference_leak_count(row) >= MIN_REFERENCE_LEAK:
            quality["leak"] += 1
        if VAGUE.search(nl):
            quality["vague"] += 1
        if len(nl) == NL_LIMIT:
            quality["at_limit"] += 1
    n = quality["n"] or 1
    print(f"[C] nl {quality['n']}행 — 누출 {quality['leak']} ({quality['leak'] / n * 100:.1f}%)"
          f" · 공허 {quality['vague']} ({quality['vague'] / n * 100:.1f}%)"
          f" · 상한절단 {quality['at_limit']} ({quality['at_limit'] / n * 100:.1f}%)")

    # [D] baseline 비교 — cond==when 채점을 행 단위로 대조
    if baseline_dir is not None:
        base_rows = load_rule_rows(baseline_dir)
        shared = rows.keys() & base_rows.keys()
        def expected_path(row: dict) -> object:
            return row.get("expected_cond", row["when"])

        same_path = [
            k for k in shared
            if expected_path(base_rows[k]) == expected_path(rows[k])
        ]
        changed_path = sorted(shared - set(same_path))

        def failure_rate(keys: list, table: dict) -> tuple[int, int]:
            def mismatched(row: dict) -> bool:
                if "expected_cond" in row:
                    return row["cond"] != row["expected_cond"]
                return set(row["cond"]) != set(row["when"])

            failed = sum(1 for k in keys if mismatched(table[k]))
            return failed, len(keys)

        for label, keys in (("경로 동일", same_path), ("경로 변경", changed_path)):
            base_failed, total = failure_rate(keys, base_rows)
            new_failed, _ = failure_rate(keys, rows)
            if total:
                print(f"[D] {label} {total}행 — cond 불일치"
                      f" base {base_failed} ({base_failed / total * 100:.2f}%)"
                      f" → now {new_failed} ({new_failed / total * 100:.2f}%)")
        only_new = len(rows.keys() - base_rows.keys())
        only_base = len(base_rows.keys() - rows.keys())
        if only_new or only_base:
            print(f"[D] 이번에만 있는 행 {only_new} · baseline 에만 있는 행 {only_base}")

    # [E][F] 원문·AST 독립 정답지 대조
    corpus_root = Path(manifest.get("corpus_root") or "")
    analysis_root = corpus_root / "analysis"
    source_root = Path(manifest.get("source_root") or (corpus_root / "source"))
    ops, calls_by_routine = _evidence_effects(run_dir)

    if analysis_root.is_dir():
        ast = _ast_facts(analysis_root)
        ast_returns = sum(len(f["returns"]) for f in ast.values())
        ast_calls = sum(len(f["calls"]) for f in ast.values())
        print(f"\n[E] RETURN — AST occurrence {ast_returns} · 행 증거 ret 효과 {ops['ret']}")
        if source_root.is_dir():
            source_total = sum(_source_return_lines(source_root).values())
            print(f"    원문 독립 계수 {source_total} (주석·블록주석·문자열 제외)")
            if ast_returns > source_total:
                print("    ⚠ AST 가 원문보다 많다 — 추가·중복 의심")
            elif ast_returns < source_total:
                print("    ⚠ AST 가 원문보다 적다 — 누락 또는 입력 statement 경계 결함 후보")
        print(f"[F] CALL — AST callsite 고유 {ast_calls} · "
              f"행 증거 call 대상 고유 {sum(len(v) for v in calls_by_routine.values())}")
        missing_routines = [
            name for name, targets in calls_by_routine.items()
            if name in ast and not targets <= ast[name]["calls"]
        ]
        if missing_routines:
            print(f"    ⚠ 증거 call 이 AST 에 없는 루틴 {len(missing_routines)}개: "
                  f"{missing_routines[:3]}")
        else:
            print("    증거 call ⊆ AST call: 위반 0")
    else:
        print(f"\n[E][F] 건너뜀 — AST 코퍼스를 찾지 못함: {analysis_root}")

    if not with_graph:
        print("\n[G][H] 건너뜀 — 그래프 축은 --graph 로 실행한다")
        return

    graph = asyncio.run(_graph_axes(None))
    expected_db = manifest.get("neo4j_database")
    actual_db = graph.get("database")
    if expected_db and actual_db and expected_db != actual_db:
        print(f"\n⚠ run 은 DB '{expected_db}' 에 썼는데 지금 '{actual_db}' 를 읽고 있다 — "
              "ROBO_NEO4J_DATABASE 를 맞춰라 (04 §4.6)")
    structure_delta = _graph_rule_structure_delta(graph["rule_rows"], rows)
    print(
        "[G] RULE 구조 — expected "
        f"{structure_delta['expected']} · graph {structure_delta['actual']} · "
        f"누락 {len(structure_delta['missing'])} · "
        f"추가 {len(structure_delta['extra'])} · "
        f"값 불일치 {len(structure_delta['mismatches'])}"
    )
    for label, values in (
        ("missing", structure_delta["missing"]),
        ("extra", structure_delta["extra"]),
        ("mismatch", structure_delta["mismatches"]),
    ):
        if values:
            print(f"    ⚠ {label} sample: {values[:3]}")
    delivered_quality = _delivered_nl_quality(graph["rule_rows"], rows)
    delivered_n = delivered_quality["n"] or 1
    print(
        f"[C-delivery] graph nl {delivered_quality['n']}행 — "
        f"누출 {delivered_quality['leak']} "
        f"({delivered_quality['leak'] / delivered_n * 100:.1f}%) · "
        f"공허 {delivered_quality['vague']} "
        f"({delivered_quality['vague'] / delivered_n * 100:.1f}%) · "
        f"상한절단 {delivered_quality['at_limit']} "
        f"({delivered_quality['at_limit'] / delivered_n * 100:.1f}%) · "
        f"미대조 {delivered_quality['unmatched']}"
    )
    print(f"\n[G] 그래프 관계 (DB={actual_db})")
    for row in graph["relations"]:
        print(f"    {row['rel']:<16} {row['n']}")
    counts = graph["rule_example"]
    orphans = (graph["orphan_example"] or {}).get("orphans")
    print(f"[H] EXAMPLE — RULE {counts.get('rules')} "
          f"(무조건 행 {counts.get('unconditional_rules')} 는 예시 없음이 계약) · "
          f"조건-only 행 {counts.get('conditional_without_outcome')} · "
          f"HAS_EXAMPLE {counts.get('example_links')} · 고아 EXAMPLE {orphans}")
    unmatched = counts.get("conditional_without_example") or 0
    if unmatched:
        print(f"    ⚠ 조건과 직접 결과가 있는 RULE 인데 EXAMPLE 없음 {unmatched}건 — 04 §3.5 위반 후보")
    else:
        print("    조건과 직접 결과가 있는 RULE 의 예시 누락 0")
    if orphans:
        print("    ⚠ 소유 RULE 없는 EXAMPLE — 04 §3.5 위반 후보")
    if gold_dir is not None:
        ddl_scope = _ddl_table_names(corpus_root / "ddl")
        gold = load_gold_axes(
            gold_dir, table_scope=ddl_scope or None, source_root=source_root,
        )
        ddl_tables, ddl_columns = _ddl_axes(corpus_root / "ddl")
        if ddl_tables:
            gold["TABLE"] = ddl_tables
            gold["COLUMN"] = ddl_columns
        actual = asyncio.run(_graph_gold_axes(source_root))
        _print_gold_comparison(gold, actual)


def main() -> None:
    args = [a for a in sys.argv[1:]]
    with_graph = "--graph" in args
    if with_graph:
        args.remove("--graph")
    baseline: Path | None = None
    if "--baseline" in args:
        index = args.index("--baseline")
        baseline = Path(args[index + 1])
        del args[index:index + 2]
    gold_dir: Path | None = None
    if "--gold" in args:
        index = args.index("--gold")
        gold_dir = Path(args[index + 1])
        del args[index:index + 2]
    run_dir = Path(args[0]) if args else latest_run_dir(Path("logs"))
    judge(run_dir, baseline, with_graph, gold_dir)


if __name__ == "__main__":
    main()
