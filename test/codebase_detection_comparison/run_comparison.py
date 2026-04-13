"""방법 A (Naive) vs 방법 B (Deep Agent) 나란히 비교.

실행:
    python -m test.codebase_detection_comparison.run_comparison
    python -m test.codebase_detection_comparison.run_comparison --target ProFrame-C
    python -m test.codebase_detection_comparison.run_comparison --only deep
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

from config.settings import settings
from test.codebase_detection_comparison.naive_detection import naive_detect
from test.codebase_detection_comparison.deep_detection import deep_detect
from test.codebase_detection_comparison.run_logger import RunLogger


TARGETS = [
    {
        "name": "ProFrame-C",
        "path": "c:/uEngine/robo/data/source/apascrt00010t02_20260406",
        "ground_truth": {
            "primary_language": "C",
            "frameworks": ["ProFrame"],
            "orms": ["ProFrame DBIO"],
            "databases": ["Oracle"],
            "proprietary": ["pfm", "pdb"],
        },
    },
    {
        "name": "robo-data-analyzer-self",
        "path": "c:/uEngine/robo/robo-data-analyzer",
        "ground_truth": {
            "primary_language": "Python",
            "frameworks": ["FastAPI", "LangChain", "DeepAgents"],
            "orms": ["Neo4j-Driver"],
            "databases": ["Neo4j"],
            "proprietary": [],
        },
    },
]


def _jaccard(expected: list[str], actual: list[str]) -> float:
    """대소문자 무시 Jaccard 유사도 (집합 기반)."""

    if not expected and not actual:
        return 1.0
    e = {x.lower() for x in expected}
    a = {x.lower() for x in actual}
    if not e:
        return 0.0 if a else 1.0
    inter = e & a
    union = e | a
    return len(inter) / len(union) if union else 0.0


def _check_mark(actual: list[str], expected: list[str]) -> str:
    """기대 정답을 모두 포함하면 ✅, 일부면 🟡, 없으면 ❌."""

    if not expected:
        return "  " if not actual else "ℹ "
    actual_lower = {x.lower() for x in actual}
    hit = sum(1 for e in expected if e.lower() in actual_lower)
    if hit == len(expected):
        return "✅"
    if hit > 0:
        return "🟡"
    return "❌"


def _print_target_header(target: dict) -> None:
    sep = "=" * 95
    print(f"\n{sep}")
    print(f"🎯 TARGET: {target['name']}")
    print(f"   PATH:   {target['path']}")
    print(f"   EXPECT: lang={target['ground_truth']['primary_language']} "
          f"fw={target['ground_truth']['frameworks']} "
          f"db={target['ground_truth']['databases']}")
    print(sep)


def _format_name_list(items: list, key: str = "name") -> list[str]:
    """방법 B 의 dict 리스트에서 name 만 추출."""

    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(str(it.get(key, "")))
        else:
            out.append(str(it))
    return [x for x in out if x]


def _print_comparison(target: dict, naive, deep) -> None:
    gt = target["ground_truth"]

    deep_fw = _format_name_list(deep.frameworks, "name")
    deep_orm = _format_name_list(deep.orms, "name")
    deep_db = _format_name_list(deep.databases, "name")
    deep_prop = _format_name_list(deep.proprietary_frameworks, "prefix")

    print(f"\n{'필드':<18} {'Naive (A)':<36} {'DeepAgent (B)':<36}")
    print(f"{'-' * 18} {'-' * 36} {'-' * 36}")

    def row(label: str, a_items: list[str], b_items: list[str], expected: list[str]):
        a_mark = _check_mark(a_items, expected)
        b_mark = _check_mark(b_items, expected)
        a_text = ", ".join(a_items)[:32] or "-"
        b_text = ", ".join(b_items)[:32] or "-"
        print(f"{label:<18} {a_mark} {a_text:<33} {b_mark} {b_text:<33}")

    row("primary_language",
        [naive.primary_language] if naive.primary_language else [],
        [deep.primary_language] if deep.primary_language else [],
        [gt["primary_language"]])
    row("frameworks", naive.frameworks, deep_fw, gt["frameworks"])
    row("orms", naive.orms, deep_orm, gt["orms"])
    row("databases", naive.databases, deep_db, gt["databases"])
    row("proprietary", naive.proprietary_frameworks, deep_prop, gt["proprietary"])

    # 메트릭.
    print(f"\n{'metric':<18} {'Naive (A)':<36} {'DeepAgent (B)':<36}")
    print(f"{'-' * 18} {'-' * 36} {'-' * 36}")
    print(f"{'tokens':<18} {naive.total_tokens:<36,} {deep.total_tokens:<36,}")
    print(f"{'elapsed_sec':<18} {naive.elapsed_sec:<36.2f} {deep.elapsed_sec:<36.2f}")
    print(f"{'tool_calls':<18} {'0':<36} {len(deep.tool_calls):<36}")

    # Jaccard.
    j_fw_a = _jaccard(gt["frameworks"], naive.frameworks)
    j_fw_b = _jaccard(gt["frameworks"], deep_fw)
    j_db_a = _jaccard(gt["databases"], naive.databases)
    j_db_b = _jaccard(gt["databases"], deep_db)
    print(f"{'jaccard(fw)':<18} {j_fw_a:<36.2f} {j_fw_b:<36.2f}")
    print(f"{'jaccard(db)':<18} {j_db_a:<36.2f} {j_db_b:<36.2f}")

    if deep.tool_calls:
        print(f"\n📋 DeepAgent 도구 호출 시퀀스 ({len(deep.tool_calls)}회):")
        for i, tc in enumerate(deep.tool_calls, 1):
            args_short = tc.args[:70] + ("..." if len(tc.args) > 70 else "")
            print(f"  [{i:>2}] {tc.tool_name}({args_short})")

    if naive.error:
        print(f"\n⚠ Naive error: {naive.error}")
    if deep.error:
        print(f"\n⚠ Deep error: {deep.error}")


async def run_single_target(target: dict, only: str | None = None) -> tuple:
    _print_target_header(target)
    path = Path(target["path"])
    if not path.exists():
        print(f"  ⚠ 경로 없음 — 스킵")
        return (None, None)

    logger = RunLogger(target["name"])
    logger.log("INFO", f"타겟 시작: {target['name']} (경로={path})")
    logger.log("INFO", "기대 정답 (ground truth)", target["ground_truth"])

    naive_result = None
    deep_result = None

    if only != "deep":
        print("\n▶ 방법 A (Naive) 실행 중...")
        naive_result = naive_detect(path, sample_count=10, logger=logger)
        logger.log_result("NAIVE", naive_result)
        print(f"  완료 — lang={naive_result.primary_language!r} "
              f"fw={naive_result.frameworks} "
              f"tokens={naive_result.total_tokens} "
              f"elapsed={naive_result.elapsed_sec:.2f}s")

    if only != "naive":
        print("\n▶ 방법 B (DeepAgent) 실행 중...")
        deep_result = await deep_detect(path, logger=logger)
        logger.log_result("DEEP", deep_result)
        print(f"  완료 — lang={deep_result.primary_language!r} "
              f"fw={[f.get('name') if isinstance(f, dict) else f for f in deep_result.frameworks]} "
              f"tokens={deep_result.total_tokens} "
              f"elapsed={deep_result.elapsed_sec:.2f}s")

    if naive_result and deep_result:
        _print_comparison(target, naive_result, deep_result)

    summary = {
        "target": target["name"],
        "naive_tokens": naive_result.total_tokens if naive_result else None,
        "naive_elapsed": naive_result.elapsed_sec if naive_result else None,
        "deep_tokens": deep_result.total_tokens if deep_result else None,
        "deep_elapsed": deep_result.elapsed_sec if deep_result else None,
        "deep_tool_calls": len(deep_result.tool_calls) if deep_result else None,
    }
    logger.close(summary=summary)
    print(f"\n📝 로그 저장: {logger.txt_path}")
    print(f"📝 JSON 저장: {logger.json_path}")

    return (naive_result, deep_result)


async def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="Codebase detection 비교 테스트")
    parser.add_argument("--target", help="특정 타겟 이름만 실행")
    parser.add_argument(
        "--only",
        choices=["naive", "deep"],
        help="한쪽 방법만 실행 (디버깅용)",
    )
    args = parser.parse_args()

    if not settings.llm.api_key:
        print("ERROR: LLM_API_KEY 가 설정되지 않았습니다 (.env 확인).")
        return

    targets = TARGETS
    if args.target:
        targets = [t for t in TARGETS if t["name"] == args.target]
        if not targets:
            print(f"ERROR: --target {args.target} 에 해당하는 타겟 없음.")
            print(f"사용 가능: {[t['name'] for t in TARGETS]}")
            return

    print("=" * 95)
    print("BL 탐지 비교: Naive (단일 LLM) vs DeepAgent (9 tools + 4-Phase)")
    print(f"모델: {settings.llm.model} | 타겟 {len(targets)}개")
    print("=" * 95)

    for target in targets:
        await run_single_target(target, only=args.only)

    print("\n" + "=" * 95)
    print("완료.")
    print("=" * 95)


if __name__ == "__main__":
    asyncio.run(main())
