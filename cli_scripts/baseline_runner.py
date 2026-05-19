"""baseline 측정 스크립트 — 여러 프로젝트 자동 실행 + ``usage_summary`` 비교.

8 프로젝트 디렉토리를 순회하며 각각 워크플로우 끝까지 실행 → ``usage_summary.json``
모아 종합 보고서 ``baseline_summaries.json`` 출력.

사용::

    cd robo-analyzer-refact
    python cli_scripts/baseline_runner.py <projects_dir>
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# robo-analyzer-refact 패키지 root 를 sys.path 에
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis_pipelines import get_pipeline
from data_models.project_and_run import Project
from shared.logging_and_stream.ndjson_stream import NDJSONStream
from shared.workflow_runner.run_executor import execute_run


# DBMS 로 인식할 확장자 (이외는 framework)
_DBMS_EXTS = {".sql", ".pkb", ".pks", ".pls", ".ddl"}


def _detect_strategy(project_dir: Path) -> str:
    """프로젝트 디렉토리 내 파일 확장자 → framework / dbms 자동 판별."""
    has_code = False
    has_only_sql = True
    for f in project_dir.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in _DBMS_EXTS:
            has_code = True
        elif ext in {".java", ".kt", ".py", ".go", ".c", ".cpp", ".ts", ".js", ".rb", ".rs", ".php"}:
            has_code = True
            has_only_sql = False
    return "dbms" if has_code and has_only_sql else "framework"


async def run_one(project_root: Path, strategy_name: str) -> dict:
    """프로젝트 1 개 실행 → ``usage_summary`` 반환."""
    pipeline = get_pipeline(strategy_name)
    project = Project(
        name=project_root.name,
        root=project_root,
        strategy=strategy_name,  # type: ignore[arg-type]
    )
    stream = NDJSONStream()
    try:
        run = await execute_run(pipeline, project, stream=stream)
    except Exception as e:
        return {"error": str(e)}
    summary_path = run.run_dir / "usage_summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    return {"warning": "no usage_summary.json"}


async def main(projects_dir: Path) -> None:
    """``projects_dir`` 안 모든 프로젝트 실행."""
    all_summaries: dict[str, dict] = {}
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        strategy = _detect_strategy(project_dir)
        print(f"\n[baseline] Running {project_dir.name} (strategy={strategy})...")
        try:
            summary = await run_one(project_dir, strategy)
            all_summaries[project_dir.name] = summary
            total = summary.get("total", {})
            print(f"  cost ${total.get('cost_usd', 0):.4f}, "
                  f"tokens={total.get('total_tokens', 0)}, "
                  f"calls={total.get('calls', 0)}")
        except Exception as e:
            print(f"  ERROR: {e}")
            all_summaries[project_dir.name] = {"error": str(e)}

    out_path = Path("baseline_summaries.json")
    out_path.write_text(
        json.dumps(all_summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[baseline] Wrote {out_path.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cli_scripts/baseline_runner.py <projects_dir>")
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1])))
