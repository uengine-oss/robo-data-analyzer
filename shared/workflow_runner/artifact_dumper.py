"""step 산출물 ``logs/analysis.run/latest/artifacts/`` 적재.

4 파일 정책 (Plan §6.3 logs 구조):

- ``ast.json``                — step1_parse_ddl + step2_load_ast 결과
                                (Schema/Table/Column/Function/Module + 모든 구조 엣지)
- ``linking.json``            — step4_linking 결과 (R/W / Calls / ReferTo / unresolved)
- ``analysis_contexts.json``  — step5_build_context 결과 (AnalysisContext per id)
- ``analysis.json``           — step6_analyze_function + step7_post_processing 결과
                                (Function summary / Module summary)

워크플로우 종료 후 ``dump_state_artifacts(run, final_state)`` 1회 호출.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from data_models.project_and_run import Run


def _artifacts_dir(run: Run) -> Path:
    d = run.run_dir / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dump_artifact(run: Run, name: str, data: Any) -> None:
    """``artifacts/<name>.json`` 에 dataclass / dict 직렬화."""
    serialized = _serialize(data)
    path = _artifacts_dir(run) / f"{name}.json"
    path.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _serialize(obj: Any) -> Any:
    """dataclass / Pydantic / dict / list 재귀 직렬화."""
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def dump_state_artifacts(run: Run, state: dict) -> None:
    """워크플로우 종료 후 state 에서 4 artifact 일괄 추출."""
    # ast.json — DDL + 소스 파싱 결과
    dump_artifact(run, "ast", {
        "schemas": state.get("schemas", []),
        "tables": state.get("tables", []),
        "columns": state.get("columns", []),
        "functions": state.get("functions", []),
        "modules": state.get("modules", []),
        "has_column_edges": state.get("has_column_edges", []),
        "belongs_to_edges": state.get("belongs_to_edges", []),
        "fk_edges": state.get("fk_edges", []),
        "parent_of_edges": state.get("parent_of_edges", []),
    })
    # linking.json — Linking 결과
    dump_artifact(run, "linking", {
        "reads_writes_edges": state.get("reads_writes_edges", []),
        "calls_edges": state.get("calls_edges", []),
        "refer_to_edges": state.get("refer_to_edges", []),
        "uses_edges": state.get("uses_edges", []),
        "unresolved_cases": state.get("unresolved_cases", []),
        "scan_result": state.get("scan_result", {}),
    })
    # analysis_contexts.json — AnalysisContext per id
    contexts = state.get("analysis_contexts", {})
    dump_artifact(run, "analysis_contexts", {
        fid: _serialize(ctx) for fid, ctx in contexts.items()
    })
    # analysis.json — 분석 결과 요약
    functions_with_summary = [
        f for f in state.get("functions", []) if getattr(f, "summary", None)
    ]
    dump_artifact(run, "analysis", {
        "functions": functions_with_summary,
        "modules": state.get("modules", []),
    })
