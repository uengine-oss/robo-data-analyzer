"""발표용 비교 데모 진입점 — 실제 테스트 데이터 폴더 스캔 방식.

❌ 나쁜 방법 (naive LLM 직접 파싱) vs ✅ 좋은 방법 (라이브러리 결정론 + LLM 의미추출)
실제 코드베이스에 있는 XML/YAML/JSON/properties 를 대상으로 비교.

실행:
  PYTHONIOENCODING=utf-8 python -m test.universal_file_parser.run_comparison
  PYTHONIOENCODING=utf-8 python -m test.universal_file_parser.run_comparison > c:/tmp/universal_parser_result.log 2>&1

환경변수:
  UFP_SCAN_DIR   : 스캔 루트 (기본 c:/uEngine/robo/테스트데이터)
  UFP_FILE_LIMIT : 최대 파일 수 (기본 14, naive 비용 제한)
"""

import os
import sys
from pathlib import Path

from .cache import init_cache, reset_cache
from .naive_llm_parse import naive_llm_parse
from .three_stage_parse import three_stage_parse


DEFAULT_SCAN_DIR = r"c:/uEngine/robo/테스트데이터"
DEFAULT_FILE_LIMIT = 20

# 데모에서 처리할 파일 후보. 라이브러리 지원/미지원 구분은
# detect.py + stage1_library.LIBRARY_HANDLERS 가 런타임에 결정 (라우팅은 여기서 안 함).
# 라이브러리가 지원하는 포맷만 요약:
#   json, yaml/yml, xml, toml, ini, dotenv, csv, dockerfile, properties, gitignore
# 그 외 (.md, .txt, .cfg, .dsl 등) 는 자동으로 stage2_codegen (LLM DeepAgent) 로 승격.
TARGET_EXTENSIONS = {
    '.xml', '.yml', '.yaml', '.json', '.toml', '.ini',
    '.properties', '.cfg', '.csv',
    '.md', '.rst', '.txt', '.markdown',
}
TARGET_FILENAMES = {
    'Dockerfile', 'dockerfile',
    '.env', '.env.local', '.env.production',
    '.gitignore', 'Makefile',
    'README', 'readme',
}
SKIP_DIR_NAMES = {
    'target', 'build', '.git', 'node_modules', '__pycache__',
    'dist', '.idea', '.vscode',
}


def scan_structured_files(root: str, limit: int) -> list[str]:
    """root 아래에서 구조화 파일을 수집. 빌드 산출물 디렉토리는 제외."""
    results: list[str] = []
    root_path = Path(root)
    if not root_path.exists():
        return results

    for path in root_path.rglob("*"):
        # 경로 중 하나라도 skip 대상이면 건너뜀
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue

        ext = path.suffix.lower()
        name = path.name
        if ext in TARGET_EXTENSIONS or name in TARGET_FILENAMES:
            results.append(str(path))
        if len(results) >= limit:
            break

    return sorted(set(results))


def _sep(char="=", width=90):
    print(char * width)


def _preview(obj, limit=200) -> str:
    s = str(obj).replace('\n', ' ')
    return s[:limit] + ("..." if len(s) > limit else "")


def _count_keys(result) -> int:
    """파싱 결과의 leaf 개수 추정."""
    if result is None:
        return 0
    if isinstance(result, dict):
        # XML flat list
        if 'elements' in result and isinstance(result['elements'], list):
            return len(result['elements'])
        # 중첩 dict
        return _count_leaves(result)
    if isinstance(result, list):
        return len(result)
    return 0


def _count_leaves(obj) -> int:
    """중첩 구조에서 leaf 값 개수."""
    if isinstance(obj, dict):
        return sum(_count_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_leaves(v) for v in obj)
    return 1


def main():
    scan_dir = os.environ.get("UFP_SCAN_DIR", DEFAULT_SCAN_DIR)
    limit = int(os.environ.get("UFP_FILE_LIMIT", DEFAULT_FILE_LIMIT))

    print(f"스캔 루트: {scan_dir}")
    print(f"최대 파일 수: {limit}")

    files = scan_structured_files(scan_dir, limit)
    if not files:
        print(f"[WARN] {scan_dir} 에서 구조화 파일을 찾지 못함")
        return

    print(f"발견된 구조화 파일: {len(files)}개\n")
    for f in files:
        size = os.path.getsize(f)
        print(f"  [{size:>8,} B] {f}")
    print()

    reset_cache()
    init_cache()

    summary = {"naive": [], "three_stage": []}

    for idx, path in enumerate(files, 1):
        size = os.path.getsize(path)
        rel = Path(path).relative_to(scan_dir) if path.startswith(scan_dir.replace("\\", "/")) else Path(path).name
        print()
        _sep()
        print(f"[{idx}/{len(files)}] {rel}  ({size:,} bytes)")
        _sep()

        # ❌ 나쁜 방법
        print("\n[나쁜 방법 — LLM 직접 파싱]")
        try:
            naive = naive_llm_parse(path)
            print(f"  시간:    {naive['elapsed_sec']}s")
            print(f"  토큰:    {naive['tokens']:,}")
            print(f"  키 개수: {_count_keys(naive['result'])}")
            print(f"  결과:    {_preview(naive['result'])}")
        except Exception as e:
            naive = {"result": {"_error": str(e)}, "elapsed_sec": 0, "tokens": 0, "strategy": "naive_llm"}
            print(f"  실행 실패: {e}")

        # ✅ 좋은 방법
        print("\n[좋은 방법 — 라이브러리 (익숙한 포맷) / DeepAgent (미지 포맷)]")
        try:
            good = three_stage_parse(path)
            print(f"  전략:    {good['strategy']}  (포맷: {good['format']})")
            print(f"  시간:    {good['elapsed_sec']}s  /  {good['tokens']:,} tokens")
            print(f"  검증:    ok={good['validation']['ok']}  reason={good['validation']['reason']}")
            print(f"  키 개수: {_count_keys(good['result'])}")
            print(f"  결과:    {_preview(good['result'])}")
        except Exception as e:
            good = {"result": None, "elapsed_sec": 0, "tokens": 0, "strategy": "failed",
                    "format": "error", "validation": {"ok": False, "reason": str(e)}}
            print(f"  실행 실패: {e}")

        summary["naive"].append({"file": str(rel), **naive})
        summary["three_stage"].append({"file": str(rel), **good})

    _print_summary_table(summary)


def _print_summary_table(summary):
    print()
    _sep()
    print("전체 비교")
    _sep()
    print(f"{'파일':<55} {'Naive 시간':>10} {'Good 시간':>10} {'Naive 토큰':>11} {'Good 토큰':>11} {'keys(N/G)':>14}")
    print("-" * 115)

    totals = {"nt": 0, "gt": 0, "ntok": 0, "gtok": 0, "na": 0, "ga": 0, "n": 0}

    for i in range(len(summary["naive"])):
        n = summary["naive"][i]
        g = summary["three_stage"][i]
        name = n['file'][-54:]
        n_anchors = _count_keys(n.get('result'))
        g_anchors = _count_keys(g.get('result'))
        print(f"{name:<55} "
              f"{n.get('elapsed_sec', 0):>9.2f}s "
              f"{g.get('elapsed_sec', 0):>9.2f}s "
              f"{n.get('tokens', 0):>11,} "
              f"{g.get('tokens', 0):>11,} "
              f"{n_anchors:>6}/{g_anchors:<6}")
        totals["nt"] += n.get('elapsed_sec', 0)
        totals["gt"] += g.get('elapsed_sec', 0)
        totals["ntok"] += n.get('tokens', 0)
        totals["gtok"] += g.get('tokens', 0)
        totals["na"] += n_anchors
        totals["ga"] += g_anchors
        totals["n"] += 1

    if totals["n"]:
        print("-" * 115)
        print(f"{'평균':<55} "
              f"{totals['nt']/totals['n']:>9.2f}s "
              f"{totals['gt']/totals['n']:>9.2f}s "
              f"{totals['ntok']/totals['n']:>11,.0f} "
              f"{totals['gtok']/totals['n']:>11,.0f} "
              f"{totals['na']/totals['n']:>6.0f}/{totals['ga']/totals['n']:<6.0f}")

        print()
        if totals["gt"]:
            print(f"속도:   {totals['nt']/totals['gt']:.1f}배 (Naive 대비 Good 이)")
        if totals["gtok"]:
            print(f"토큰:   Naive 총 {totals['ntok']:,}  vs  Good 총 {totals['gtok']:,}  ({totals['ntok']/max(totals['gtok'],1):.1f}배)")
        if totals["na"]:
            print(f"키 개수: Naive 총 {totals['na']}  vs  Good 총 {totals['ga']}  (Good 이 {totals['ga']/max(totals['na'],1):.1f}배 더 뽑음)")


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    main()
