"""DeepAgent 정확도 측정 — ground truth 기반.

samples/expected/ 에 있는 *.expected.json 파일들을 자동 발견해서
samples/test_project/ 에서 대응 파일을 찾아 파싱 → 정확도 비교.

라이브러리 경로는 stdlib 호출이라 검증 가치 낮음 → 제외.
이 스크립트는 **DeepAgent 경로만** 측정.

실행:
  PYTHONIOENCODING=utf-8 python -m test.universal_file_parser.run_accuracy_test
"""

import json
import os
import sys
from pathlib import Path

from .log_setup import setup_logging
from .metrics import measure_accuracy
from .parse import parse


MODULE_DIR = Path(__file__).parent
PROJECT_DIR = MODULE_DIR / "samples" / "test_project"
EXPECTED_DIR = MODULE_DIR / "samples" / "expected"


def _discover_deepagent_targets() -> list[tuple[Path, Path]]:
    """expected/*.expected.json 을 기반으로 (원본 파일, ground truth) 쌍 자동 발견."""
    targets = []
    if not EXPECTED_DIR.exists():
        return targets

    for exp in sorted(EXPECTED_DIR.glob("*.expected.json")):
        stem = exp.name.replace(".expected.json", "")
        # test_project 하위 전체에서 파일명 매칭
        matches = list(PROJECT_DIR.rglob(stem))
        if matches:
            targets.append((matches[0], exp))
        else:
            print(f"⚠️  ground truth 있지만 원본 못 찾음: {stem}")
    return targets


def main():
    log_path = setup_logging()
    print(f"로그: {log_path}")

    targets = _discover_deepagent_targets()
    if not targets:
        print("⚠️  DeepAgent 테스트 대상 없음. samples/expected/ 에 *.expected.json 을 추가하세요.")
        return

    print(f"DeepAgent 정확도 측정 ({len(targets)} 대상)")
    print()

    results = []
    for src_path, expected_path in targets:
        print(f"▶ {src_path.name}")
        with open(expected_path, encoding='utf-8') as f:
            expected = json.load(f)

        try:
            out = parse(str(src_path))
            acc = measure_accuracy(out["result"], expected)
            print(f"  strategy={out['strategy']}, tokens={out['tokens']}, "
                  f"elapsed={out['elapsed_sec']}s, "
                  f"F1={acc['f1']:.3f}, 값일치={acc['value_exact_match_rate']:.3f}")
        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {e}")
            acc = {"precision": 0, "recall": 0, "f1": 0, "value_exact_match_rate": 0,
                   "missed_keys": [], "extra_keys": [], "error": str(e)}
            out = {"strategy": "error", "tokens": 0, "elapsed_sec": 0}

        results.append({
            "file": src_path.name,
            "strategy": out.get("strategy", "?"),
            "tokens": out.get("tokens", 0),
            "elapsed_sec": out.get("elapsed_sec", 0),
            "accuracy": acc,
        })

    _print_report(results)


def _print_report(results: list):
    print()
    print("=" * 95)
    print("📊 DeepAgent 정확도 집계")
    print("=" * 95)
    print(f"{'파일':<30} {'전략':<15} {'토큰':>8} {'시간':>7} {'F1':>8} {'값일치':>8}")
    print("-" * 95)

    for r in results:
        a = r['accuracy']
        print(f"{r['file']:<30} {r['strategy']:<15} "
              f"{r['tokens']:>8,} {r['elapsed_sec']:>6.1f}s "
              f"{a['f1']:>8.3f} {a['value_exact_match_rate']:>8.3f}")

    if not results:
        return

    n = len(results)
    avg = lambda key: sum(r['accuracy'][key] for r in results) / n
    print("-" * 95)
    print(f"{'평균':<30} {'':<15} "
          f"{sum(r['tokens'] for r in results) // n:>8,} "
          f"{sum(r['elapsed_sec'] for r in results) / n:>6.1f}s "
          f"{avg('f1'):>8.3f} {avg('value_exact_match_rate'):>8.3f}")

    # missed/extra key 상세
    print()
    print("🔍 missed / extra keys")
    print("-" * 95)
    for r in results:
        a = r['accuracy']
        missed = a.get('missed_keys', [])
        extra = a.get('extra_keys', [])
        if missed or extra:
            print(f"  {r['file']}:")
            if missed:
                print(f"    missed: {missed[:5]}")
            if extra:
                print(f"    extra:  {extra[:5]}")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    main()
