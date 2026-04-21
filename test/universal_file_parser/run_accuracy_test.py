"""Ground truth 기반 정확도 측정

각 샘플에 대해 expected.json 이 있으면 precision/recall/F1/값일치율 계산.
발표에서 "정확도 X%" 주장의 근거.
"""

import json
import os
import sys
from pathlib import Path

from .cache import init_cache, reset_cache
from .metrics import measure_accuracy
from .naive_llm_parse import naive_llm_parse
from .three_stage_parse import three_stage_parse


MODULE_DIR = Path(__file__).parent
SAMPLES_DIR = MODULE_DIR / "samples"

SAMPLES = [
    "standard/application.yml",
    "standard/config.json",
    "standard/services.xml",
    "standard/entitymodel.xml",
    "standard/sqlmap_ReferenceInformation.xml",
    "semi_structured/Dockerfile",
    "semi_structured/.env",
    "semi_structured/sample.csv",
    "unknown/kv_custom.cfg",
    "unknown/tree_custom.dsl",
    "unknown/record_log.dat",
]


def main():
    reset_cache()
    init_cache()

    results = []

    for rel_path in SAMPLES:
        abs_path = str(SAMPLES_DIR / rel_path)
        expected_path = abs_path + ".expected.json"

        if not os.path.exists(abs_path):
            print(f"⚠️  {rel_path}: 샘플 파일 없음")
            continue
        if not os.path.exists(expected_path):
            print(f"⚠️  {rel_path}: ground truth 없음, 건너뜀")
            continue

        with open(expected_path, encoding='utf-8') as f:
            expected = json.load(f)

        # Naive
        try:
            naive_out = naive_llm_parse(abs_path)
            naive_acc = measure_accuracy(naive_out["result"], expected)
        except Exception as e:
            naive_acc = {"precision": 0, "recall": 0, "f1": 0, "value_exact_match_rate": 0,
                         "missed_keys": [], "extra_keys": [], "error": str(e)}

        # Three-stage
        try:
            good_out = three_stage_parse(abs_path)
            good_acc = measure_accuracy(good_out["result"], expected)
        except Exception as e:
            good_acc = {"precision": 0, "recall": 0, "f1": 0, "value_exact_match_rate": 0,
                        "missed_keys": [], "extra_keys": [], "error": str(e)}

        results.append({
            "file": os.path.basename(rel_path),
            "naive": naive_acc,
            "three_stage": good_acc,
        })

    _print_report(results)


def _print_report(results):
    print()
    print("=" * 95)
    print("📊 정확도 비교 (Ground Truth 기반)")
    print("=" * 95)
    header = f"{'파일':<30} {'Naive F1':>10} {'Naive 값일치':>14} {'Good F1':>10} {'Good 값일치':>14}"
    print(header)
    print("─" * 95)

    for r in results:
        print(f"{r['file']:<30} "
              f"{r['naive']['f1']:>10.3f} "
              f"{r['naive']['value_exact_match_rate']:>14.3f} "
              f"{r['three_stage']['f1']:>10.3f} "
              f"{r['three_stage']['value_exact_match_rate']:>14.3f}")

    if not results:
        print("(결과 없음)")
        return

    avg = lambda key, strat: sum(r[strat][key] for r in results) / len(results)

    print("─" * 95)
    print(f"{'평균':<30} "
          f"{avg('f1', 'naive'):>10.3f} "
          f"{avg('value_exact_match_rate', 'naive'):>14.3f} "
          f"{avg('f1', 'three_stage'):>10.3f} "
          f"{avg('value_exact_match_rate', 'three_stage'):>14.3f}")

    # 상세 진단
    print()
    print("🔍 상세 (missed / extra keys)")
    print("─" * 95)
    for r in results:
        naive_missed = r['naive'].get('missed_keys', [])
        good_missed = r['three_stage'].get('missed_keys', [])
        if naive_missed or good_missed:
            print(f"  {r['file']}:")
            if naive_missed:
                print(f"    Naive missed: {naive_missed[:3]}")
            if good_missed:
                print(f"    Good missed:  {good_missed[:3]}")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    main()
