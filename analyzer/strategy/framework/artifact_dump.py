"""Framework 분석 단계별 산출물 덤프. run_dir에 exp_run과 동일 구조로 저장.

run_dir/
  analyzer.log
  artifacts/
    00_pipeline_flow.md
    step1_catalog.json, step1_plan.json
    step2_*.json, step2_chunk_bundles_manifest.json
    step3_*.json, step4_*.json
    step5_*.json, step6_*.json
  chunk_bundles/
    *.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

PIPELINE_FLOW_MD = """# Framework 파이프라인 흐름

## Step1 스캔
- **입력**: data_dir (source, analysis/nontarget)
- **산출물**: catalog (FileCatalogEntry), plan (ProcessingPlan)

## Step2 AST
- **입력**: plan.ast_jobs
- **산출물**: function_facts_list, module_index_ast, variable_facts_list, ChunkBundle → run_dir/chunk_bundles

## Step3 아티팩트
- **입력**: xml_sqlmap, yaml, properties, xml_artifact (ejb-jar, entitymodel 등)
- **산출물**: sql_chunks, config_chunks, artifact_chunks

## Step4 인덱스
- **산출물**: sql_index, module_index, config_index, artifact_index

## Step5 링킹
- **처리**: REFER_TO 매칭, SQL→Table(LLM), Call batch, analysis LLM
- **산출물**: refer_to_edges, reads_writes, function_table_edges, module_table_edges, analysis_by_function, calls, uses

## Step6 요약·후처리
- **산출물**: analysis_by_function(보강), summary_by_variable, summary_by_module, user_stories
"""


def _to_serializable(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_serializable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {_key_str(k): _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "__dict__"):
        return _to_serializable(getattr(obj, "__dict__", str(obj)))
    return str(obj)


def _key_str(k: Any) -> str:
    if isinstance(k, tuple):
        return str(k)
    return k


def _write_json(path: Path, data: Any, ensure_ascii: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=ensure_ascii, indent=2),
        encoding="utf-8",
    )


def _sort_by_start_line(items: List[Any]) -> List[Any]:
    if not items:
        return items
    def key_fn(x: Any) -> tuple:
        if isinstance(x, dict):
            path = x.get("source_path") or x.get("file_path") or ""
            line = x.get("start_line", 0)
        else:
            path = getattr(x, "source_path", None) or getattr(x, "file_path", "")
            line = getattr(x, "start_line", 0)
        return (path, line)
    return sorted(items, key=key_fn)


def _write_pipeline_flow_md(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "00_pipeline_flow.md").write_text(PIPELINE_FLOW_MD.strip(), encoding="utf-8")
    logger.info("[PHASE:ARTIFACT] 00_pipeline_flow.md 저장 | path=%s", out_dir / "00_pipeline_flow.md")


def dump_phase1_artifacts(state: Dict[str, Any], run_dir: Path) -> None:
    """Phase1(Step1~4) 산출물을 run_dir/artifacts/에 저장."""
    run_dir = Path(run_dir)
    base = run_dir / "artifacts"
    base.mkdir(parents=True, exist_ok=True)
    _write_pipeline_flow_md(base)

    # Step1
    catalog = getattr(state, "catalog", None)
    plan = getattr(state, "plan", None)
    if catalog is not None:
        _write_json(base / "step1_catalog.json", _to_serializable(catalog), ensure_ascii=False)
    if plan is not None:
        plan_dict = asdict(plan) if is_dataclass(plan) else plan
        _write_json(base / "step1_plan.json", _to_serializable(plan_dict), ensure_ascii=False)

    # Step2
    function_facts_list = getattr(state, "function_facts_list", None) or []
    module_index_ast = getattr(state, "module_index_ast", None) or {}
    ref_candidates = getattr(state, "ref_candidates_by_function", None) or {}
    if function_facts_list:
        _write_json(base / "step2_function_facts.json", _to_serializable(function_facts_list), ensure_ascii=False)
    if module_index_ast:
        _write_json(base / "step2_module_index_ast.json", _to_serializable(module_index_ast), ensure_ascii=False)
    if ref_candidates:
        summary = {k: len(v) for k, v in ref_candidates.items()}
        _write_json(base / "step2_ref_candidates_summary.json", summary, ensure_ascii=False)
    chunk_bundle_dir = run_dir / "chunk_bundles"
    if chunk_bundle_dir.is_dir():
        manifest = []
        try:
            from analyzer.strategy.framework.parsing.chunk_bundle import load_chunk_bundle
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                try:
                    bundle = load_chunk_bundle(str(p))
                    manifest.append({
                        "file": p.name,
                        "path": str(p.relative_to(run_dir)),
                        "file_path": getattr(bundle.file_meta, "file_path", ""),
                        "methods": len(bundle.methods),
                        "function_calls": len(bundle.function_calls),
                        "local_vars": len(bundle.local_vars),
                    })
                except Exception as e:
                    manifest.append({"file": p.name, "path": str(p.relative_to(run_dir)), "error": str(e)})
        except Exception:
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                manifest.append({"file": p.name, "path": str(p.relative_to(run_dir))})
        if manifest:
            _write_json(base / "step2_chunk_bundles_manifest.json", manifest, ensure_ascii=False)

    # Step3
    for name, key in [
        ("step3_sql_chunks.json", "sql_chunks"),
        ("step3_config_chunks.json", "config_chunks"),
        ("step3_artifact_chunks.json", "artifact_chunks"),
    ]:
        val = getattr(state, key, None)
        if val is not None:
            sorted_val = _sort_by_start_line(val) if isinstance(val, list) else val
            _write_json(base / name, _to_serializable(sorted_val), ensure_ascii=False)

    # Step4
    for name, key in [
        ("step4_sql_index.json", "sql_index"),
        ("step4_module_index.json", "module_index"),
        ("step4_config_index.json", "config_index"),
        ("step4_artifact_index.json", "artifact_index"),
    ]:
        val = getattr(state, key, None)
        if val is not None and isinstance(val, dict):
            _write_json(base / name, _to_serializable(val), ensure_ascii=False)

    logger.info("[PHASE:ARTIFACT] Phase1 산출물 저장 | dir=%s", base)


def dump_phase2_artifacts(state: Dict[str, Any], run_dir: Path) -> None:
    """Phase2(Step5~6 링킹·요약) 산출물을 run_dir/artifacts/에 저장."""
    run_dir = Path(run_dir)
    base = run_dir / "artifacts"
    base.mkdir(parents=True, exist_ok=True)

    for name, key in [
        ("step5_refer_to_edges.json", "refer_to_edges"),
        ("step5_reads_writes.json", "reads_writes"),
        ("step5_unresolved.json", "unresolved"),
        ("step5_fk_edges.json", "fk_edges"),
        ("step5_column_refs.json", "column_refs"),
        ("step5_function_table_edges.json", "function_table_edges"),
        ("step5_module_table_edges.json", "module_table_edges"),
        ("step5_summary_by_function.json", "analysis_by_function"),
        ("step5_calls_edges_method.json", "calls_edges"),
        ("step5_uses_edges.json", "uses_edges"),
        ("step5_unresolved_calls.json", "unresolved_calls"),
    ]:
        val = getattr(state, key, None)
        if val is not None:
            _write_json(base / name, _to_serializable(val), ensure_ascii=False)

    analysis_by_function = getattr(state, "analysis_by_function", None) or {}
    if analysis_by_function:
        _write_json(base / "step6_summary_by_function.json", _to_serializable(analysis_by_function), ensure_ascii=False)
    unresolved_calls = getattr(state, "unresolved_calls", None)
    if unresolved_calls is not None:
        _write_json(base / "step6_unresolved_calls.json", _to_serializable(unresolved_calls), ensure_ascii=False)

    logger.info("[PHASE:ARTIFACT] Phase2 산출물 저장 | dir=%s", base)


def dump_phase3_artifacts(state: Dict[str, Any], run_dir: Path) -> None:
    """Phase3(Field/Class 요약, User Story) 산출물을 run_dir/artifacts/에 저장."""
    run_dir = Path(run_dir)
    base = run_dir / "artifacts"
    base.mkdir(parents=True, exist_ok=True)

    summary_by_variable = getattr(state, "summary_by_variable", None) or {}
    if summary_by_variable:
        _write_json(base / "step6_summary_by_variable.json", _to_serializable(summary_by_variable), ensure_ascii=False)
    summary_by_module = getattr(state, "summary_by_module", None) or {}
    if summary_by_module:
        _write_json(base / "step6_summary_by_module.json", _to_serializable(summary_by_module), ensure_ascii=False)

    logger.info("[PHASE:ARTIFACT] Phase3 산출물 저장 | dir=%s", base)
