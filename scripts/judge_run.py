"""run 판정 도구 — 헌법 IX 3층 중 1·2층(자동 검사 가능한 축)의 상설 소유자.

절차 정본은 `docs/handovers/analysis-performance-validation-canonical/04-검증방법.md`.
새 검증 축이 생기면 일회용 스크립트를 쓰지 말고 여기에 추가해 매 run 상시로 돌게 한다.

사용:
    .venv\\Scripts\\python.exe scripts\\judge_run.py                  # 최신 run
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir>
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --baseline <run_dir>
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --graph  # 그래프 대조 축 포함

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RUN_DIR_NAME = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")
REF = re.compile(r"\b[CE]\d{1,4}\b")
VAGUE = re.compile(r"해당 케이스를 처리|조건이 성립하면|다음 단계로 진행한다$|처리하고 종료한다")
NL_LIMIT = 300


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
    if isinstance(response, dict):
        response = response.get("parsed") or response.get("raw") or response
    if isinstance(response, dict) and "content" in response:
        try:
            response = json.loads(response["content"])
        except (TypeError, json.JSONDecodeError):
            return {}
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


def load_rule_rows(run_dir: Path) -> dict[tuple[str, str], dict]:
    """record 에서 (target_id, R키) → {cond, then, nl, when, direct} 를 전수 수집."""
    rows: dict[tuple[str, str], dict] = {}
    for record_path in sorted((run_dir / "llm-prompts").glob("*rules*.jsonl")):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                evidence_rows = _evidence_rows(record.get("user_content") or "")
                if not evidence_rows:
                    continue
                for row_key, rule in _response_rules(record.get("response")).items():
                    row = evidence_rows.get(row_key)
                    if row is None:
                        continue
                    rows[(record["target_id"], row_key)] = {
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
            elif ntype == "FUNCTION_CALL" and node.get("name"):
                # 대소문자는 호출 동일성의 근거가 아니다 — 운영 채점기(rule_grading)도
                # casefold 로 비교한다. 여기서 구별하면 매크로 표기 차이가 전부 오탐이 된다.
                current["calls"].add(str(node["name"]).casefold())
        for child in node.get("children") or ():
            if isinstance(child, dict):
                walk(child, current, file_name)

    for path in sorted(corpus_root.glob("*.json")):
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
    keyword = re.compile(r"(?<![A-Za-z0-9_])(return|throw|raise)(?![A-Za-z0-9_])")
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
            total += len(keyword.findall(line_comment.sub("", line)))
        counts[path.name] = total
    return counts


def _evidence_effects(run_dir: Path) -> tuple[Counter, dict[str, set[str]]]:
    """행 증거의 효과를 집계한다 — op 별 개수와 target별 호출 대상 집합."""
    ops: Counter = Counter()
    calls_by_target: dict[str, set[str]] = defaultdict(set)
    for record_path in sorted((run_dir / "llm-prompts").glob("*rules*.jsonl")):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                content = record.get("user_content") or ""
                decoder = json.JSONDecoder()
                cursor = 0
                routine = str(record.get("target_id") or "").split(":")[-1]
                while True:
                    head = content.find('{"conditions"', cursor)
                    if head < 0:
                        break
                    try:
                        evidence, consumed = decoder.raw_decode(content[head:])
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
        # cond 가 빈 규칙(무조건 실행 행)은 예시를 만들지 않는 것이 계약이다
        # (example_synthesis.synthesize_examples: 빈 cond → []). 그 914건을 위반으로
        # 세면 진짜 신호가 묻힌다 — 조건이 있는데 예시가 없는 것만 위반 후보다.
        rule_example = await one(
            "MATCH (r:RULE) "
            "OPTIONAL MATCH (r)-[:HAS_EXAMPLE]->(e:EXAMPLE) "
            "WITH r, count(e) AS examples, "
            "     (r.cond IS NULL OR r.cond = '') AS unconditional "
            "RETURN count(r) AS rules, "
            "       sum(CASE WHEN unconditional THEN 1 ELSE 0 END) AS unconditional_rules, "
            "       sum(CASE WHEN NOT unconditional AND examples = 0 THEN 1 ELSE 0 END) "
            "         AS conditional_without_example, "
            "       sum(examples) AS example_links"
        )
        orphan_example = await one(
            "MATCH (e:EXAMPLE) WHERE NOT ()-[:HAS_EXAMPLE]->(e) "
            "RETURN count(e) AS orphans"
        )
        return {
            "database": getattr(client, "_database", None),
            "labels": counts,
            "relations": rels,
            "rule_example": rule_example[0] if rule_example else {},
            "orphan_example": orphan_example[0] if orphan_example else {},
        }
    finally:
        await client.close()


def judge(run_dir: Path, baseline_dir: Path | None, with_graph: bool = False) -> None:
    print("=" * 72)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"run: {manifest['run_id']}  corpus: {Path(manifest['corpus_root']).name}")

    # [A] 회계
    obligation = sum(
        len(json.loads(p.read_text(encoding="utf-8")))
        for p in (run_dir / "branch-tables").glob("*.json")
    )
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
        if len(REF.findall(nl)) >= 2:
            quality["leak"] += 1
        if VAGUE.search(nl):
            quality["vague"] += 1
        if len(nl) >= NL_LIMIT:
            quality["at_limit"] += 1
    n = quality["n"] or 1
    print(f"[C] nl {quality['n']}행 — 누출 {quality['leak']} ({quality['leak'] / n * 100:.1f}%)"
          f" · 공허 {quality['vague']} ({quality['vague'] / n * 100:.1f}%)"
          f" · 상한절단 {quality['at_limit']} ({quality['at_limit'] / n * 100:.1f}%)")

    # [D] baseline 비교 — cond==when 채점을 행 단위로 대조
    if baseline_dir is not None:
        base_rows = load_rule_rows(baseline_dir)
        shared = rows.keys() & base_rows.keys()
        same_path = [k for k in shared if base_rows[k]["when"] == rows[k]["when"]]
        changed_path = sorted(shared - set(same_path))

        def failure_rate(keys: list, table: dict) -> tuple[int, int]:
            failed = sum(
                1 for k in keys if set(table[k]["cond"]) != set(table[k]["when"])
            )
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
            print(f"    원문 독립 계수 {source_total} (주석·블록주석 제외, 문자열 포함 상한)")
            if ast_returns > source_total:
                print("    ⚠ AST 가 원문보다 많다 — 추가·중복 의심")
        print(f"[F] CALL — AST FUNCTION_CALL 고유 {ast_calls} · "
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
    print(f"\n[G] 그래프 관계 (DB={actual_db})")
    for row in graph["relations"]:
        print(f"    {row['rel']:<16} {row['n']}")
    counts = graph["rule_example"]
    orphans = (graph["orphan_example"] or {}).get("orphans")
    print(f"[H] EXAMPLE — RULE {counts.get('rules')} "
          f"(무조건 행 {counts.get('unconditional_rules')} 는 예시 없음이 계약) · "
          f"HAS_EXAMPLE {counts.get('example_links')} · 고아 EXAMPLE {orphans}")
    unmatched = counts.get("conditional_without_example") or 0
    if unmatched:
        print(f"    ⚠ 조건 있는 RULE 인데 EXAMPLE 없음 {unmatched}건 — 04 §3.5 위반 후보")
    else:
        print("    조건 있는 RULE 의 예시 누락 0")
    if orphans:
        print("    ⚠ 소유 RULE 없는 EXAMPLE — 04 §3.5 위반 후보")


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
    run_dir = Path(args[0]) if args else latest_run_dir(Path("logs"))
    judge(run_dir, baseline, with_graph)


if __name__ == "__main__":
    main()
