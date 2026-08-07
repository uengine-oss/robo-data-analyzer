"""3층 직접 의미 판정의 표본 고정 (04 §4.1 — 결과 열람 전에 규칙과 ID 를 박는다).

선정 규칙 (2026-08-07 사용자 확정):
  1. 어려운 것을 기반으로 뽑는다 — 쉬운 함수로 통과율을 부풀리지 않는다.
  2. stereotype 이 겹치지 않게 배정한다 — 유형마다 대표 1개.
  3. 겹칠 수밖에 없으면 그 유형 안에서 가장 어려운 것을 고른다.
  4. 난도 점수 = 분기표 행수 + 3×최대 경로 깊이 + 5×finding 수 + 2×(R/W 보유) + 파일 분산.

사용:
    .venv\\Scripts\\python.exe scripts\\fix_semantic_sample.py <run_dir> <출력.json>

출력 JSON 에 선정 근거(점수 구성)까지 남겨 사후에 "왜 이 20개인가"를 재현할 수 있게 한다.
그래프(stereotype·R/W)는 읽기 전용으로 조회한다 — DB 는 ROBO_NEO4J_DATABASE.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE_SIZE = 20


def per_function_rows(run_dir: Path) -> dict[tuple[str, str], int]:
    """(file, function) → 행수. 함수명만 키로 쓰면 파일 간 동명 함수가 합쳐진다
    (실측: b000_main_proc 가 여러 파일에 존재해 신원이 섞였다)."""
    rows: dict[tuple[str, str], int] = {}
    for path in (run_dir / "branch-tables").glob("*.json"):
        # 파일명: code_<file>_<function>.json — 확장자에 밑줄은 없다(\w 는 밑줄을 포함해
        # 함수명을 확장자로 삼켜 'dt' 같은 꼬리만 남긴 실측 오류가 있었다).
        name = path.stem
        match = re.match(r"code_(.+?\.[A-Za-z0-9]+)_(.+)$", name)
        if not match:
            continue
        rows[(match.group(1), match.group(2))] = len(
            json.loads(path.read_text(encoding="utf-8"))
        )
    return rows


def per_function_depth(run_dir: Path) -> dict[tuple[str, str], int]:
    decoder = json.JSONDecoder()
    depth: dict[tuple[str, str], int] = defaultdict(int)
    for record_path in (run_dir / "llm-prompts").glob("*rules*.jsonl"):
        with record_path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                parts = str(record.get("target_id") or "").split(":")
                function = (parts[-2], parts[-1]) if len(parts) >= 3 else ("?", parts[-1])
                content = record.get("user_content") or ""
                cursor = 0
                while True:
                    head = content.find('{"conditions"', cursor)
                    if head < 0:
                        break
                    try:
                        evidence, used = decoder.raw_decode(content[head:])
                    except json.JSONDecodeError:
                        cursor = head + 1
                        continue
                    cursor = head + used
                    for row in (evidence.get("rows") or {}).values():
                        depth[function] = max(
                            depth[function], len(row.get("when") or ())
                        )
    return dict(depth)


def per_function_findings(run_dir: Path) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    findings = run_dir / "analysis-findings.jsonl"
    if findings.exists():
        with findings.open(encoding="utf-8") as fh:
            for line in fh:
                parts = str(json.loads(line).get("target_id") or "").split("::")[0].split(":")
                if len(parts) >= 3:
                    counts[(parts[-2], parts[-1])] += 1
    return dict(counts)


async def graph_traits() -> dict[tuple[str, str], dict]:
    from graph.neo4j_client import Neo4jClient

    client = Neo4jClient()
    try:
        rows = await client.run_read_query(
            "MATCH (f:FUNCTION) "
            "OPTIONAL MATCH (f)-[rw:READS|WRITES]->(:TABLE) "
            "RETURN f.name AS name, f.id AS id, f.stereotype AS stereotype, count(rw) AS rw",
            {},
        )
    finally:
        await client.close()
    traits: dict[tuple[str, str], dict] = {}
    for r in rows:
        parts = str(r["id"] or "").split(":")
        file_name = parts[-2] if len(parts) >= 3 else "?"
        traits[(file_name, str(r["name"]))] = {
            "stereotype": str(r["stereotype"] or "?"), "rw": int(r["rw"]),
        }
    return traits


def main() -> None:
    run_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    rows = per_function_rows(run_dir)
    depth = per_function_depth(run_dir)
    findings = per_function_findings(run_dir)
    traits = asyncio.run(graph_traits())

    scored = []
    for key, row_count in rows.items():
        trait = traits.get(key) or {"stereotype": "?", "rw": 0}
        score = (
            row_count
            + 3 * depth.get(key, 0)
            + 5 * findings.get(key, 0)
            + 2 * (1 if trait["rw"] else 0)
        )
        scored.append({
            "file": key[0],
            "function": key[1],
            "stereotype": trait["stereotype"],
            "score": score,
            "rows": row_count,
            "max_depth": depth.get(key, 0),
            "findings": findings.get(key, 0),
            "rw": trait["rw"],
        })
    scored.sort(key=lambda item: -item["score"])

    picked: list[dict] = []
    used_types: set[str] = set()
    # 1차: stereotype 비중복 — 유형별 최고 난도
    for item in scored:
        if len(picked) >= SAMPLE_SIZE:
            break
        if item["stereotype"] not in used_types:
            used_types.add(item["stereotype"])
            picked.append(item)
    # 2차: 남는 자리는 전체 최난도 순 (유형 중복 허용 — 규칙 3)
    for item in scored:
        if len(picked) >= SAMPLE_SIZE:
            break
        if item not in picked:
            picked.append(item)

    out = {
        "rule": ("어려운 것 우선 · stereotype 비중복 우선 배정 · 겹치면 유형 내 최난도 "
                 "(점수 = 행수 + 3×깊이 + 5×finding + 2×RW)"),
        "run": run_dir.name,
        "sample": picked,
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"표본 {len(picked)}개 고정 → {out_path}")
    for item in picked:
        print(f"  {item['score']:>4}  {item['stereotype']:<20} "
              f"{item['file']}:{item['function']}"
              f"  (행{item['rows']}·깊이{item['max_depth']}·f{item['findings']})")


if __name__ == "__main__":
    main()
