"""spec 045 + 046 E2E 검증 — 실제 분석 1회 결과를 기준선·요구사항과 대조.

사용::

    .venv/Scripts/python.exe cli_scripts/verify_045_046.py

판정 기준은 **추측이 아니라 실측 기준선**이다:
- 결정론 구조 라벨 분포 = 리팩터 **전** 실제 DB에서 뽑은 값 (046 SC-001 동작 불변)
- LLM 산출물(RULE/EXAMPLE/QUESTION)은 run 마다 달라지므로 **비교 대상 아님**
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from neo4j import AsyncGraphDatabase  # noqa: E402

from system.env_catalog import ENV  # noqa: E402


# ── 리팩터 전 실측 기준선 (2026-07-13, neo4j DB) ──────────────────────
# 결정론 구조 라벨만. LLM 산출물은 제외 (run 마다 다름).
BASELINE_STRUCTURE = {
    "DEFINE": 164,
    "COLUMN": 153,
    "FUNCTION": 127,
    "MEMBER": 55,
    "CONSTANT_FIELD": 40,
    "TABLE": 22,
    "GLOBAL_VARIABLE": 17,
    "FILE": 12,
    "STRUCT": 9,
    "EXTERNAL": 6,
    "MODULE": 3,
}
LLM_LABELS = {"RULE", "EXAMPLE", "QUESTION", "GLOSSARY", "EMBED_META"}

ART = Path("logs/analysis.run/latest/artifacts")
LOG = Path("logs/analysis.run/latest/analyzer.log")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


async def graph_checks() -> dict:
    uri = os.getenv("ROBO_NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.getenv("ROBO_NEO4J_USER", "neo4j")
    pw = os.getenv("ROBO_NEO4J_PASSWORD", "")
    db = os.getenv("ROBO_NEO4J_DATABASE", "neo4j")
    print(f"\n[Neo4j] {uri}  db={db}\n")

    driver = AsyncGraphDatabase.driver(uri, auth=(user, pw))
    try:
        async with driver.session(database=db) as s:
            r = await s.run(
                "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c ORDER BY c DESC"
            )
            counts = {rec["l"]: rec["c"] for rec in [x async for x in r]}

            r = await s.run(
                "MATCH (t:TABLE) WHERE t.logical_name IS NOT NULL RETURN count(t) AS c"
            )
            tbl_ln = (await r.single())["c"]

            r = await s.run("MATCH (n) WHERE n.code_text IS NOT NULL RETURN count(n) AS c")
            with_code = (await r.single())["c"]
    finally:
        await driver.close()

    print("── 046 SC-001: 동작 불변 (결정론 구조 라벨) ──")
    for label, expected in BASELINE_STRUCTURE.items():
        actual = counts.get(label, 0)
        check(f"{label:<16} 기준 {expected:>4} / 실제 {actual:>4}", actual == expected)

    print("\n── LLM 산출물 (비교 대상 아님 — 참고) ──")
    for label in sorted(LLM_LABELS):
        if label in counts:
            print(f"     {label:<16} {counts[label]}")

    print("\n── 045: 그래프 정합 ──")
    check("TABLE.logical_name 보유 = 22", tbl_ln == 22, f"실제 {tbl_ln}")
    check("code_text 보유 노드 존재", with_code > 0, f"{with_code}개")
    return counts


def artifact_checks() -> None:
    print("\n── 045: artifact 정합 ──")
    if not ART.is_dir():
        check("artifacts 디렉토리", False, f"{ART} 없음")
        return

    ast = json.loads((ART / "ast.json").read_text(encoding="utf-8"))
    ana = json.loads((ART / "analysis.json").read_text(encoding="utf-8"))

    # FR-014 — analysis.json 에 원문 없음
    def ct(d: dict, key: str) -> int:
        return sum(len(x.get("code_text", "") or "") for x in d.get(key, []))

    ana_code = ct(ana, "code_nodes") + ct(ana, "modules")
    ast_code = ct(ast, "code_nodes") + ct(ast, "modules")
    check("FR-014 analysis.json 원문 0바이트", ana_code == 0,
          f"analysis={ana_code:,}B / ast={ast_code:,}B (전: 842,440B 중복)")

    # FR-013 — ast.json 의 logical_name 이 채워짐
    tables = ast.get("tables", [])
    with_ln = [t for t in tables if t.get("logical_name")]
    check("FR-013 ast.json logical_name 채워짐",
          len(tables) > 0 and len(with_ln) == len(tables),
          f"{len(with_ln)}/{len(tables)} (전: 0/22)")

    # FR-015 — 유령 키 없음 → graph_extras 비어있거나 없음
    extras = ART / "graph_extras.json"
    empty = (not extras.exists()) or (not json.loads(extras.read_text(encoding="utf-8")))
    check("FR-015 graph_extras 비어있음", empty)

    print(f"\n     artifact 크기: " + ", ".join(
        f"{p.name} {p.stat().st_size / 1024:,.0f}KB" for p in sorted(ART.glob("*.json"))
    ))


def log_checks() -> None:
    print("\n── 045: 조용한 실패 표면화 (로그) ──")
    if not LOG.is_file():
        check("analyzer.log", False, "없음")
        return
    text = LOG.read_text(encoding="utf-8", errors="ignore")

    # FR-005 — 원문 누락 노드가 0 이어야 한다.
    #   ★ 주의: 이 레포의 로거는 **0 인 kwargs 를 의도적으로 생략**한다(logger.py:335 —
    #     노이즈 제거 규약). 따라서 "code_text_missing 문자열이 로그에 있다" 를 통과조건으로
    #     삼으면 안 된다 (정상일수록 안 찍힌다). 실제 표면화 장치는 **log_warning** 이므로,
    #     그 경고가 없다 = 누락 0 이다.
    check("FR-005 원문 누락 노드 0 (code_text_missing 경고 없음)",
          "code_text_missing" not in text,
          "경고가 뜨면 소스를 못 읽은 것")

    # FR-004/006 — 정상 데이터셋이므로 read 실패 경고가 없어야 함
    check("FR-004 source_read_failed 0건", "source_read_failed" not in text)

    # FR-008 — 집계 불일치 경고 없어야 함
    check("FR-008 node_accounting_mismatch 0건", "node_accounting_mismatch" not in text)
    check("FR-008 nodes_unanalyzed 0건", "nodes_unanalyzed" not in text)

    # 새 ERROR 없어야 함
    errs = [l for l in text.splitlines() if " ERROR " in l]
    check("ERROR 로그 0건", not errs, f"{len(errs)}건" if errs else "")
    for e in errs[:3]:
        print(f"       {e[:120]}")


async def main() -> int:
    print("=" * 74)
    print("  spec 045 + 046 — E2E 검증")
    print("=" * 74)

    await graph_checks()
    artifact_checks()
    log_checks()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 74)
    print(f"  결과: {passed}/{total} 통과")
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print("\n  ❌ 실패 항목:")
        for f in failed:
            print(f"     - {f}")
    print("=" * 74)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
