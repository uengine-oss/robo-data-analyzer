"""커버리지 잔차 감사 집계 (골키퍼 그물2) — run 산출물 `coverage/*.json` 전수 요약.

사용: .venv\\Scripts\\python.exe scripts\\audit_coverage.py [run_dir=최신 run]

인자를 생략하면 `logs/` 아래 최신 run 폴더(run_id 이름)를 자동 선택한다.

함수별 잔차를 shape(구문 모양)로 묶어 "어떤 미지 구문이 몇 곳에서 안 덮였는가"를
corpus 단위로 보여준다. 위험 구문(unsupported_constructs) 장부도 함께 집계한다.
새 corpus·새 언어를 물릴 때 이 표부터 본다 — 라벨 67행 소실 계열 사고의 사전 검출기.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_RUN_DIR_NAME = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")


def _latest_run_dir(root: Path) -> Path:
    runs = sorted(
        entry for entry in root.iterdir()
        if entry.is_dir() and _RUN_DIR_NAME.match(entry.name)
    ) if root.is_dir() else []
    if not runs:
        raise SystemExit(f"run 폴더가 없다: {root} — 분석 run 이후에 실행하라")
    return runs[-1]


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_run_dir(Path("logs"))
    coverage_dir = run_dir / "coverage"
    files = sorted(coverage_dir.glob("*.json"))
    if not files:
        print(f"coverage 산출물이 없다: {coverage_dir} — 분석 run 이후에 실행하라")
        return 1

    total_live = 0
    total_covered = 0
    shape_counts: Counter[str] = Counter()
    shape_samples: dict[str, list[str]] = defaultdict(list)
    unsupported: Counter[str] = Counter()
    worst: list[tuple[float, str, int]] = []

    for path in files:
        report = json.loads(path.read_text(encoding="utf-8"))
        total_live += report["live"]
        total_covered += report["covered"]
        for entry in report["residue"]:
            shape_counts[entry["shape"]] += 1
            if len(shape_samples[entry["shape"]]) < 3:
                shape_samples[entry["shape"]].append(
                    f"{report['function']}:{entry['line']} {entry['text']}"
                )
        for entry in report["unsupported"]:
            unsupported[entry["kind"]] += 1
        if report["live"]:
            worst.append(
                (report["ratio"], report["function"], len(report["residue"]))
            )

    ratio = total_covered / total_live if total_live else 1.0
    print(f"functions: {len(files)} | live lines: {total_live} | "
          f"covered: {total_covered} ({ratio:.1%}) | residue: {total_live - total_covered}")

    if shape_counts:
        print("\n잔차 shape 상위 (미지 구문은 무리로 드러난다):")
        for shape, count in shape_counts.most_common(15):
            print(f"  {count:4}  {shape}")
            for sample in shape_samples[shape]:
                print(f"          예: {sample}")
    else:
        print("\n잔차 0 — 모든 live 줄이 결정론 사실로 덮였다")

    if unsupported:
        print("\n위험 구문 장부 (값 추적 금지 — 표면화만):")
        for kind, count in unsupported.most_common():
            print(f"  {count:4}  {kind}")

    print("\n잔차 비율 하위 함수:")
    for ratio_value, function, residue_count in sorted(worst)[:10]:
        if residue_count:
            print(f"  {ratio_value:6.1%}  잔차 {residue_count:3}  {function}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
