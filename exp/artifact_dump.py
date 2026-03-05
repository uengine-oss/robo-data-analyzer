"""단계별 산출물 덤프 및 파이프라인 흐름 문서 생성. --output-run-dir 사용 시 호출."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PIPELINE_FLOW_MD = """# 파이프라인 흐름 (파일 → 라이브러리 → 처리)

## Step1 스캔
- **입력**: data_dir
- **라이브러리**: pathlib / os.walk, chardet(인코딩)
- **처리**: 확장자+시그니처로 분류 (java_ast, xml_sqlmap, yaml, properties, xml_ejb, xml_entitymodel, etc)
- **산출물**: catalog (FileCatalogEntry 리스트), plan (ProcessingPlan: java_ast_jobs, xml_sqlmap_jobs, ...)

## Step2 Java AST
- **입력**: plan.java_ast_jobs (파일 경로)
- **라이브러리**: json 로드, exp.ast.FrameworkAstProcessor
- **처리**: AST 순회 → MethodFacts, class_index_ast, symbol_type_index, method_index, ref_candidates_by_method. data_dir 있으면 ChunkBundle 생성
- **산출물**: method_facts_list, class_index_ast, field_facts_list, ChunkBundle → data_dir/chunk_bundles/*.json

## Step3 아티팩트
- **입력**: plan.xml_sqlmap_jobs, yaml_jobs, properties_jobs, xml_ejb_jobs, xml_entitymodel_jobs
- **라이브러리**: MyBatis XML → lxml.etree | YAML → ruamel.yaml 또는 PyYAML | properties → 내장 파싱 | ejb/entitymodel → lxml
- **산출물**: sql_chunks (SqlStatementChunk), config_chunks (ConfigSectionChunk), artifact_chunks (ArtifactChunk)

## Step4 인덱스
- **입력**: sql_chunks, method_facts_list, class_index_ast, config_chunks, artifact_chunks, data_dir(chunk_bundles)
- **처리**: dict 빌드 (sql_key→SqlStatementChunk 등), build_bundle_span_index
- **산출물**: sql_index, class_index, config_index, artifact_index, span_index, chunk_index

## Step5 링킹
- **입력**: method_facts_list, sql_index, config_index, class_index, sql_chunks, field_chunks
- **처리**: 결정론 매칭(키 in 코드), SQL→테이블(LLM)·Call batch 병렬, analysis LLM(메서드 단위 병렬), resolve_calls_and_uses, call_batch와 병합
- **산출물**: refer_to_edges, reads_writes, fk_edges, column_refs, method_table_edges, unresolved, summary_by_method, calls_edges_method, uses_edges → Neo4j(table/REFER_TO/READS_WRITES/FK/CALLS/USES)

## DeepAgent (조건부)
- **입력**: unresolved (List[UnresolvedLink])
- **조건**: unresolved 존재 및 skip_deepagent=False, LLM API 키 설정
- **라이브러리**: get_llm(), deepagents.create_deep_agent
- **산출물**: new_refer_to_edges, new_method_table_edges → refer_to_edges에 병합

## Step6 역할/요약
- **입력**: summary_by_method, calls_edges_method (Step5 결과), method_facts_list
- **처리**: 호출 그래프 기반 보강(리프→상위, 동일 레벨 병렬), method_summary_enhancement
- **산출물**: summary_by_method(보강 후), unresolved_calls → Neo4j(method summary만)
"""


def _to_serializable(obj: Any) -> Any:
    """dataclass → dict, 리스트/딕셔너리 재귀 변환. tuple 키는 문자열로."""
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
    """dict 키가 tuple이면 문자열로 변환 (JSON 호환)."""
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
    """start_line(및 source_path/file_path) 기준 정렬. dict 또는 dataclass."""
    if not items:
        return items
    def key_fn(x: Any) -> tuple:
        if isinstance(x, dict):
            path = x.get("source_path") or x.get("file_path") or ""
            line = x.get("start_line", 0)
        else:
            path = getattr(x, "source_path", None) or getattr(x, "file_path", "") or ""
            line = getattr(x, "start_line", 0)
        return (path, line)
    return sorted(items, key=key_fn)


def write_pipeline_flow_md(out_dir: Path) -> None:
    """out_dir/00_pipeline_flow.md 에 흐름 문서 저장."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "00_pipeline_flow.md").write_text(PIPELINE_FLOW_MD.strip(), encoding="utf-8")
    logger.info("[EXP:ARTIFACT] 00_pipeline_flow.md 저장 | path=%s", out_dir / "00_pipeline_flow.md")


def dump_step1_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step1 산출물만 저장 (catalog, plan). 스텝 완료 직후 호출."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_pipeline_flow_md(out_dir)
    catalog = state.get("catalog")
    plan = state.get("plan")
    if catalog is not None:
        _write_json(out_dir / "step1_catalog.json", _to_serializable(catalog), ensure_ascii=False)
    if plan is not None:
        plan_dict = asdict(plan) if is_dataclass(plan) else plan
        _write_json(out_dir / "step1_plan.json", _to_serializable(plan_dict), ensure_ascii=False)
    if catalog is not None or plan is not None:
        logger.info("[EXP:ARTIFACT] Step1 산출물 저장 | dir=%s", out_dir)


def dump_step2_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step2 산출물만 저장. 스텝 완료 직후 호출. out_dir이 run_dir/artifacts 이면 chunk_bundles는 run_dir/chunk_bundles 참조."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    method_facts_list = state.get("method_facts_list") or []
    class_index_ast = state.get("class_index_ast") or {}
    ref_candidates = state.get("ref_candidates_by_method") or {}
    if method_facts_list:
        _write_json(out_dir / "step2_method_facts.json", _to_serializable(method_facts_list), ensure_ascii=False)
    if class_index_ast:
        _write_json(out_dir / "step2_class_index_ast.json", _to_serializable(class_index_ast), ensure_ascii=False)
    if ref_candidates:
        summary = {k: len(v) for k, v in ref_candidates.items()}
        _write_json(out_dir / "step2_ref_candidates_summary.json", summary, ensure_ascii=False)
    chunk_bundle_dir = out_dir.parent / "chunk_bundles"
    run_dir_for_relative = out_dir.parent  # chunk_bundles는 run_dir 아래에 있으므로 path는 run_dir 기준
    if chunk_bundle_dir.is_dir():
        manifest = []
        try:
            from exp.chunk_bundle import load_chunk_bundle
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                try:
                    bundle = load_chunk_bundle(p)
                    manifest.append({
                        "file": p.name,
                        "path": str(p.relative_to(run_dir_for_relative)),
                        "file_path": getattr(bundle.file_meta, "file_path", ""),
                        "methods": len(bundle.methods),
                        "method_calls": len(bundle.method_calls),
                        "variables": len(bundle.variables),
                    })
                except Exception as e:
                    manifest.append({"file": p.name, "path": str(p.relative_to(run_dir_for_relative)), "error": str(e)})
        except ImportError:
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                manifest.append({"file": p.name, "path": str(p.relative_to(run_dir_for_relative))})
        if manifest:
            _write_json(out_dir / "step2_chunk_bundles_manifest.json", manifest, ensure_ascii=False)
    if method_facts_list or class_index_ast or ref_candidates:
        logger.info("[EXP:ARTIFACT] Step2 산출물 저장 | dir=%s", out_dir)


def dump_step3_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step3 산출물만 저장 (sql_chunks, config_chunks, artifact_chunks). start_line 기준 정렬 후 저장."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, key in [("step3_sql_chunks.json", "sql_chunks"), ("step3_config_chunks.json", "config_chunks"), ("step3_artifact_chunks.json", "artifact_chunks")]:
        val = state.get(key)
        if val is not None and isinstance(val, list):
            sorted_val = _sort_by_start_line(val)
            _write_json(out_dir / name, _to_serializable(sorted_val), ensure_ascii=False)
            written += 1
        elif val is not None:
            _write_json(out_dir / name, _to_serializable(val), ensure_ascii=False)
            written += 1
    if written:
        logger.info("[EXP:ARTIFACT] Step3 산출물 저장 | dir=%s files=%d", out_dir, written)


def dump_step4_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step4 산출물만 저장. 스텝 완료 직후 호출."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, key in [
        ("step4_sql_index.json", "sql_index"),
        ("step4_class_index.json", "class_index"),
        ("step4_config_index.json", "config_index"),
        ("step4_artifact_index.json", "artifact_index"),
    ]:
        val = state.get(key)
        if val is not None and isinstance(val, dict):
            _write_json(out_dir / name, _to_serializable(val), ensure_ascii=False)
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    if span_index or chunk_index:
        summary_lines = [
            f"span_index keys: {len(span_index)}",
            f"chunk_index keys: {len(chunk_index)}",
        ]
        (out_dir / "step4_span_chunk_summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")
    logger.info("[EXP:ARTIFACT] Step4 산출물 저장 | dir=%s", out_dir)


def dump_step5_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step5 산출물만 저장. 스텝 완료 직후 호출."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, key in [
        ("step5_refer_to_edges.json", "refer_to_edges"),
        ("step5_reads_writes.json", "reads_writes"),
        ("step5_unresolved.json", "unresolved"),
        ("step5_fk_edges.json", "fk_edges"),
        ("step5_method_table_edges.json", "method_table_edges"),
        ("step5_summary_by_method.json", "summary_by_method"),
        ("step5_calls_edges_method.json", "calls_edges_method"),
        ("step5_uses_edges.json", "uses_edges"),
    ]:
        val = state.get(key)
        if val is not None:
            _write_json(out_dir / name, _to_serializable(val), ensure_ascii=False)
    logger.info("[EXP:ARTIFACT] Step5 산출물 저장 | dir=%s", out_dir)


def dump_deepagent_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """DeepAgent 산출물만 저장."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    new_refer = state.get("deepagent_new_refer_to_edges")
    new_mt = state.get("deepagent_new_method_table_edges")
    if new_refer is not None:
        _write_json(out_dir / "step_deepagent_new_refer_to.json", _to_serializable(new_refer), ensure_ascii=False)
    if new_mt is not None:
        _write_json(out_dir / "step_deepagent_new_method_table_edges.json", _to_serializable(new_mt), ensure_ascii=False)
    if new_refer is not None or new_mt is not None:
        logger.info("[EXP:ARTIFACT] DeepAgent 산출물 저장 | dir=%s", out_dir)


def dump_step6_artifacts(state: Dict[str, Any], out_dir: Path) -> None:
    """Step6 산출물만 저장 (보강 후 summary, field/class summary, unresolved_calls). CALLS/USES는 Step5 산출."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_by_method = state.get("summary_by_method") or {}
    if summary_by_method:
        _write_json(out_dir / "step6_summary_by_method.json", _to_serializable(summary_by_method), ensure_ascii=False)
    summary_by_field = state.get("summary_by_field") or {}
    if summary_by_field:
        _write_json(out_dir / "step6_summary_by_field.json", _to_serializable(summary_by_field), ensure_ascii=False)
    summary_by_class = state.get("summary_by_class") or {}
    if summary_by_class:
        _write_json(out_dir / "step6_summary_by_class.json", _to_serializable(summary_by_class), ensure_ascii=False)
    unresolved_calls = state.get("unresolved_calls")
    if unresolved_calls is not None:
        _write_json(out_dir / "step6_unresolved_calls.json", _to_serializable(unresolved_calls), ensure_ascii=False)
    user_stories = state.get("user_stories") or []
    if user_stories:
        _write_json(out_dir / "step6_user_stories.json", _to_serializable(user_stories), ensure_ascii=False)
    logger.info("[EXP:ARTIFACT] Step6 산출물 저장 | dir=%s", out_dir)


def dump_artifacts(state: Dict[str, Any], out_dir: Path, options: Optional[Dict[str, Any]] = None) -> None:
    """state에서 단계별 산출물을 out_dir/artifacts/에 JSON(및 요약 txt)로 저장. 00_pipeline_flow.md 생성."""
    options = options or {}
    out_dir = Path(out_dir)
    base = out_dir / "artifacts"
    base.mkdir(parents=True, exist_ok=True)
    write_pipeline_flow_md(base)

    # Step1
    catalog = state.get("catalog")
    plan = state.get("plan")
    if catalog is not None:
        _write_json(base / "step1_catalog.json", _to_serializable(catalog), ensure_ascii=False)
    if plan is not None:
        plan_dict = asdict(plan) if is_dataclass(plan) else plan
        _write_json(base / "step1_plan.json", _to_serializable(plan_dict), ensure_ascii=False)

    # Step2
    method_facts_list = state.get("method_facts_list") or []
    class_index_ast = state.get("class_index_ast") or {}
    ref_candidates = state.get("ref_candidates_by_method") or {}
    if method_facts_list:
        _write_json(base / "step2_method_facts.json", _to_serializable(method_facts_list), ensure_ascii=False)
    if class_index_ast:
        _write_json(base / "step2_class_index_ast.json", _to_serializable(class_index_ast), ensure_ascii=False)
    if ref_candidates:
        summary = {k: len(v) for k, v in ref_candidates.items()}
        _write_json(base / "step2_ref_candidates_summary.json", summary, ensure_ascii=False)
    chunk_bundle_dir = out_dir / "chunk_bundles"
    if chunk_bundle_dir.is_dir():
        manifest = []
        try:
            from exp.chunk_bundle import load_chunk_bundle
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                try:
                    bundle = load_chunk_bundle(p)
                    manifest.append({
                        "file": p.name,
                        "path": str(p.relative_to(out_dir)),
                        "file_path": getattr(bundle.file_meta, "file_path", ""),
                        "methods": len(bundle.methods),
                        "method_calls": len(bundle.method_calls),
                        "variables": len(bundle.variables),
                    })
                except Exception as e:
                    manifest.append({"file": p.name, "path": str(p.relative_to(out_dir)), "error": str(e)})
        except ImportError:
            for p in sorted(chunk_bundle_dir.glob("*.json")):
                manifest.append({"file": p.name, "path": str(p.relative_to(out_dir))})
        if manifest:
            _write_json(base / "step2_chunk_bundles_manifest.json", manifest, ensure_ascii=False)
            logger.info("[EXP:ARTIFACT] step2_chunk_bundles_manifest.json 저장 | entries=%d", len(manifest))

    # Step3 (start_line 정렬)
    for name, key in [("step3_sql_chunks.json", "sql_chunks"), ("step3_config_chunks.json", "config_chunks"), ("step3_artifact_chunks.json", "artifact_chunks")]:
        val = state.get(key)
        if val is not None:
            sorted_val = _sort_by_start_line(val) if isinstance(val, list) else val
            _write_json(base / name, _to_serializable(sorted_val), ensure_ascii=False)

    # Step4
    for name, key in [
        ("step4_sql_index.json", "sql_index"),
        ("step4_class_index.json", "class_index"),
        ("step4_config_index.json", "config_index"),
        ("step4_artifact_index.json", "artifact_index"),
    ]:
        val = state.get(key)
        if val is not None and isinstance(val, dict):
            _write_json(base / name, _to_serializable(val), ensure_ascii=False)
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    if span_index or chunk_index:
        summary_lines = [
            f"span_index keys: {len(span_index)}",
            f"chunk_index keys: {len(chunk_index)}",
        ]
        (base / "step4_span_chunk_summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    # Step5
    for name, key in [
        ("step5_refer_to_edges.json", "refer_to_edges"),
        ("step5_reads_writes.json", "reads_writes"),
        ("step5_unresolved.json", "unresolved"),
        ("step5_fk_edges.json", "fk_edges"),
        ("step5_method_table_edges.json", "method_table_edges"),
        ("step5_summary_by_method.json", "summary_by_method"),
        ("step5_calls_edges_method.json", "calls_edges_method"),
        ("step5_uses_edges.json", "uses_edges"),
    ]:
        val = state.get(key)
        if val is not None:
            _write_json(base / name, _to_serializable(val), ensure_ascii=False)

    # DeepAgent (선택)
    new_refer = state.get("deepagent_new_refer_to_edges")
    new_mt = state.get("deepagent_new_method_table_edges")
    if new_refer is not None:
        _write_json(base / "step_deepagent_new_refer_to.json", _to_serializable(new_refer), ensure_ascii=False)
    if new_mt is not None:
        _write_json(base / "step_deepagent_new_method_table_edges.json", _to_serializable(new_mt), ensure_ascii=False)

    # Step6 (보강 후 summary, unresolved_calls만; CALLS/USES는 Step5)
    summary_by_method = state.get("summary_by_method") or {}
    if summary_by_method:
        _write_json(base / "step6_summary_by_method.json", _to_serializable(summary_by_method), ensure_ascii=False)
    unresolved_calls = state.get("unresolved_calls")
    if unresolved_calls is not None:
        _write_json(base / "step6_unresolved_calls.json", _to_serializable(unresolved_calls), ensure_ascii=False)

    logger.info("[EXP:ARTIFACT] 산출물 덤프 완료 | dir=%s", base)
