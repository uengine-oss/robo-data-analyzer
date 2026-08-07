"""run 판정 도구 — 회계·nl 품질·(선택) 직전 run 과의 행 단위 비교.

사용:
    .venv\\Scripts\\python.exe scripts\\judge_run.py                 # 최신 run
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir>
    .venv\\Scripts\\python.exe scripts\\judge_run.py <run_dir> --baseline <run_dir>

판정 축 (골드셋 없이 결정론 — 08 §20.0 기법)
  [A] 회계    : 분기표 행 수(obligation) — 결정론 예측치와 일치해야 구조가 안 깨진 것
  [B] findings: 품질 격리 상태별 집계 — 정확도 축
  [C] nl 품질 : 참조번호 누출·공허 서술·상한 절단 — record 응답 전수
  [D] 비교    : baseline run 과 공통 행의 cond 채점을 대조 — 경로 동일/변경 분리
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

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


def judge(run_dir: Path, baseline_dir: Path | None) -> None:
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


def main() -> None:
    args = [a for a in sys.argv[1:]]
    baseline: Path | None = None
    if "--baseline" in args:
        index = args.index("--baseline")
        baseline = Path(args[index + 1])
        del args[index:index + 2]
    run_dir = Path(args[0]) if args else latest_run_dir(Path("logs"))
    judge(run_dir, baseline)


if __name__ == "__main__":
    main()
