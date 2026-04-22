"""프로젝트 단위 데모 진입점.

실행:
  PYTHONIOENCODING=utf-8 python -m test.universal_file_parser.run_project_demo

환경변수:
  UFP_PROJECT : 분석할 프로젝트 폴더 (기본: samples/mini_project)
  UFP_TARGET_EXTS : 제외할 타겟 언어 확장자 (예: ".c,.h")
"""

import os
import sys
from pathlib import Path


DEFAULT_PROJECT = str(
    Path(__file__).parent / "samples" / "test_project"
)
DEFAULT_TARGET_EXTS = {'.c', '.h'}


def main():
    from test.universal_file_parser.log_setup import setup_logging
    log_path = setup_logging()
    print(f"로그: {log_path}")

    project_path = os.environ.get("UFP_PROJECT", DEFAULT_PROJECT)

    target_exts_env = os.environ.get("UFP_TARGET_EXTS")
    if target_exts_env:
        target_exts = {e.strip() for e in target_exts_env.split(",") if e.strip()}
    else:
        target_exts = DEFAULT_TARGET_EXTS

    print(f"프로젝트 분석: {project_path}")
    print(f"타겟 언어 (제외): {sorted(target_exts)}")
    print()

    from test.universal_file_parser.project_parser import analyze_project

    result = analyze_project(
        project_path=project_path,
        target_extensions=target_exts,
    )

    _print_report(result)


def _print_report(result: dict):
    s = result['summary']

    print("=" * 72)
    print("처리 결과")
    print("=" * 72)
    print(f"  전체 파일:           {result['total_files']}")
    print(f"  분석 대상:           {result['scanned_files']}")
    print(f"  ├ 경로 1 (라이브러리): {s['library_path']}")
    print(f"  ├ 경로 2 (DeepAgent): {s['deepagent_path']}")
    print(f"  └ raw fallback:      {s['raw_fallback']}")
    print()
    print(f"  LLM 호출:    {s['total_llm_calls']} 회")
    print(f"  총 토큰:     {s['total_tokens']:,}")
    print(f"  총 시간:     {s['elapsed_sec']} 초")
    print()

    print("-" * 72)
    print("파일별 전략")
    print("-" * 72)
    for r in result['results']:
        path = r.get('_path') or (
            r.get('result', {}).get('_path', '?') if isinstance(r.get('result'), dict) else '?'
        )
        rel = _rel(path)
        strategy = r.get('strategy', '?')
        ok = '✓' if r.get('validation', {}).get('ok') else '✗'
        print(f"  {ok} [{strategy:<20}] {rel}")


def _rel(path: str) -> str:
    """프로젝트 루트 기준 상대 경로."""
    try:
        return os.path.relpath(path, os.environ.get("UFP_PROJECT", DEFAULT_PROJECT))
    except Exception:
        return path


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    main()
