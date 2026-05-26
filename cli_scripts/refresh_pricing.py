"""LLM 가격 갱신 보조 도구 — 현재 yaml 표 + 공식 페이지 URL + 갱신 안내.

사용::

    python cli_scripts/refresh_pricing.py

이 스크립트는 자동 갱신은 하지 않는다 (가격 페이지 HTML 구조 변경·자동 추출 오류
위험 회피 — 운영자가 페이지를 직접 보고 yaml 수동 편집이 안전).

흐름:
1. ``shared/llm/llm_pricing.yaml`` 현재 가격 표 + ``last_updated`` 출력
2. 공식 가격 페이지 URL 3개 안내
3. 갱신 절차 가이드 (yaml 직접 편집 → ``last_updated`` 갱신 → commit)

새 모델 추가도 같은 흐름 — yaml 의 ``models:`` 아래 1 블록 추가만.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Windows cp949 콘솔에서 한글/특수문자 (— ★ 등) UnicodeEncodeError 방지.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PRICING_YAML = _ROOT / "shared" / "llm" / "llm_pricing.yaml"


def main() -> None:
    """yaml 로드 → 표 출력 → 공식 페이지 URL → 갱신 안내."""
    if not _PRICING_YAML.is_file():
        print(f"ERROR: PRICING yaml 없음: {_PRICING_YAML}")
        sys.exit(1)

    data = yaml.safe_load(_PRICING_YAML.read_text(encoding="utf-8"))
    models = data.get("models") or {}
    last_updated = data.get("last_updated", "(없음)")
    sources = data.get("sources") or []

    print(f"=== 현재 PRICING ({_PRICING_YAML.relative_to(_ROOT)}) ===")
    print(f"last_updated: {last_updated}")
    print(f"등록 모델 수: {len(models)}")
    print()
    print(f"{'Model':<38} {'input/1M':>10} {'output/1M':>11} {'cached/1M':>11}")
    print("-" * 73)
    for name, price in models.items():
        cached = price.get("cached_input_per_1m", 0.0)
        print(
            f"{name:<38} "
            f"{price['input_per_1m']:>10.4f} "
            f"{price['output_per_1m']:>11.4f} "
            f"{cached:>11.4f}"
        )

    print()
    print("=== 공식 가격 페이지 ===")
    for url in sources:
        print(f"  {url}")

    print()
    print("=== 갱신 절차 ===")
    print(f"  1. 위 페이지에서 현재 운영 중인 모델의 input/output/cached 가격 확인")
    print(f"  2. {_PRICING_YAML.relative_to(_ROOT)} 직접 편집")
    print(f"  3. last_updated 갱신 (예: 2026-MM-DD)")
    print(f"  4. git commit -m 'chore: PRICING 갱신 (YYYY-MM-DD 시점 공식가)'")
    print()
    print("  ※ provider 별 가격 차이 — 같은 모델도 OpenAI 직접 vs OpenRouter 경유 가격 다름.")
    print("    운영 중인 provider 가격을 우선.")


if __name__ == "__main__":
    main()
