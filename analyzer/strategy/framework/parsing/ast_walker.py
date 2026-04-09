"""Step 2: AST 순회 및 FunctionFacts/ChunkBundle 생성 (범용)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from analyzer.strategy.framework._ast_core import (
    FrameworkAstProcessor,
    MODULE_TYPES,
    VARIABLE_TYPES,
    FUNCTION_TYPES,
)
from analyzer.strategy.framework.common.ontology import get_ontology
from analyzer.strategy.framework.common.models import (
    VariableFacts,
    FunctionFacts,
    NODE_VARIABLE,
    NODE_METHOD_CALL,
    NODE_NEW_INSTANCE,
)
from util.encoding_utils import read_text_with_fallback_sync as read_text_with_fallback, read_json_with_fallback
from .chunk_bundle import (
    build_chunk_bundle_from_source,
    save_chunk_bundle,
    _get_chunk_bundle_dir_from_run_dir,
)

logger = logging.getLogger(__name__)


@dataclass
class AstVisitorContext:
    symbol_type_index: Dict[str, Dict[str, str]] = field(default_factory=dict)
    function_index: Dict[str, str] = field(default_factory=dict)
    function_index_by_module: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    variable_types_by_module: Dict[str, Dict[str, str]] = field(default_factory=dict)
    chunk_functions: List[Dict[str, Any]] = field(default_factory=list)
    chunk_function_calls: List[Dict[str, Any]] = field(default_factory=list)
    chunk_variables: List[Dict[str, Any]] = field(default_factory=list)
    ref_candidates_by_function: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    function_call_seq: Dict[str, int] = field(default_factory=dict)
    node_kinds: Dict[str, str] = field(default_factory=dict)  # function_id → 원래 노드 타입 (FUNCTION, METHOD 등)


def _last_line_from_ast(ast_data: dict) -> int:
    """AST에서 마지막 라인 번호 추출."""
    last = 0
    for child in ast_data.get("children", []):
        end = child.get("endLine", 0)
        if end > last:
            last = end
    return last


def load_ast_json(path: str) -> dict:
    """AST JSON 파일 로드 (인코딩 fallback)."""
    return read_json_with_fallback(path)


def _visit_ast_for_indices(
    node: Dict[str, Any],
    package_name: Optional[str],
    module_name: Optional[str],
    module_fqn: Optional[str],
    function_id: Optional[str],
    ctx: AstVisitorContext,
    lang: str,
) -> None:
    """Raw AST 순회: symbol_type_index, function_index, ChunkBundle용 리스트 수집 (범용)."""
    onto = get_ontology()
    module_types = onto.module_types(lang)
    function_types = onto.function_types(lang)
    call_types = onto.call_types(lang)

    node_type = node.get("type", "")
    start_line = node.get("startLine", 0)
    end_line = node.get("endLine", 0)
    children = node.get("children", [])

    if node_type == "FILE":
        pkg = node.get("packageName") or node.get("filePath", "").replace("\\", "/")
        if "/" in pkg:
            pkg = ".".join(pkg.rsplit("/", 1)[0].split("/"))
        if pkg:
            package_name = pkg

        # C 언어: FILE이 container일 때 파일명을 module_fqn으로 등록
        if node_type in module_types:
            file_path = node.get("filePath", "")
            file_basename = file_path.replace("\\", "/").rsplit("/", 1)[-1] if file_path else ""
            file_stem = file_basename.rsplit(".", 1)[0] if "." in file_basename else file_basename
            if file_stem:
                module_name = file_stem
                module_fqn = f"{package_name}.{file_stem}" if package_name else file_stem

    if node_type in module_types and node_type != "FILE":
        module_name = node.get("name") or module_name
        if package_name and module_name:
            module_fqn = f"{package_name}.{module_name}"
        else:
            module_fqn = module_name

    if node_type in function_types:
        function_name = node.get("name", "")
        if module_fqn and function_name:
            function_id = f"{module_fqn}.{function_name}"
        elif package_name and function_name:
            # fallback: package.functionName
            function_id = f"{package_name}.{function_name}"
        else:
            function_id = function_id or f"unknown_{start_line}"
        if function_id and function_id not in ctx.function_index:
            ctx.function_index[function_id] = function_id
            ctx.node_kinds[function_id] = node_type  # FUNCTION, METHOD 등 원래 타입 저장
        if module_fqn and function_name:
            ctx.function_index_by_module.setdefault(module_fqn, {}).setdefault(function_name, []).append(function_id)
        ctx.chunk_functions.append({"function_id": function_id, "start_line": start_line, "end_line": end_line})

    for ch in children:
        ch_type = ch.get("type", "")
        ch_start = ch.get("startLine", 0)
        ch_end = ch.get("endLine", 0)

        if ch_type == NODE_VARIABLE and function_id:
            var_name = ch.get("name", "")
            var_type = ch.get("variableType")
            if ch.get("initializerContainsNewInstance"):
                for sub in ch.get("children", []):
                    if isinstance(sub, dict) and sub.get("type") == NODE_NEW_INSTANCE:
                        var_type = sub.get("name") or var_type
                        break
            if var_name:
                ctx.symbol_type_index.setdefault(function_id, {})[var_name] = var_type or ""
            flags: Dict[str, Any] = {}
            if ch.get("initializerContainsMethodCall"):
                flags["initializerContainsMethodCall"] = True
            if ch.get("initializerContainsNewInstance"):
                flags["initializerContainsNewInstance"] = True
            ctx.chunk_variables.append(
                {
                    "parent_function_id": function_id,
                    "var_name": var_name,
                    "var_type": var_type,
                    "start_line": ch_start,
                    "end_line": ch_end,
                    "flags": flags if flags else None,
                }
            )

        # GLOBAL_VARIABLE: 함수 밖 변수 → __global__ 키에 수집
        if ch_type == NODE_VARIABLE and not function_id:
            var_name = ch.get("name", "")
            var_type = ch.get("variableType")
            if var_name:
                ctx.symbol_type_index.setdefault("__global__", {})[var_name] = var_type or ""

        if ch_type in call_types and function_id:
            seq = ctx.function_call_seq.get(function_id, 0)
            call_id = f"C:{ch_start}:{ch_end}:{seq}"
            ctx.function_call_seq[function_id] = seq + 1
            ctx.chunk_function_calls.append(
                {
                    "parent_function_id": function_id,
                    "call_id": call_id,
                    "start_line": ch_start,
                    "end_line": ch_end,
                }
            )
            ctx.ref_candidates_by_function.setdefault(function_id, []).append(
                {
                    "kind": "CALL_REF",
                    "receiver": None,
                    "function_name": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        if ch_type == NODE_NEW_INSTANCE and function_id:
            ctx.ref_candidates_by_function.setdefault(function_id, []).append(
                {
                    "kind": "NEW_INSTANCE_REF",
                    "moduleName": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        _visit_ast_for_indices(ch, package_name, module_name, module_fqn, function_id, ctx, lang=lang)


def collect_ast_indices_and_chunk_data(
    ast_data: dict,
    lang: str,
) -> Tuple[
    Dict[str, Dict[str, str]],
    Dict[str, str],
    Dict[str, Dict[str, List[str]]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, List[Dict[str, Any]]],
    Dict[str, str],
]:
    """AST에서 symbol_type_index, function_index, function_index_by_module, ChunkBundle용 functions/calls/variables, ref_candidates_by_function 반환."""
    ctx = AstVisitorContext()

    if ast_data.get("type") == "FILE":
        _visit_ast_for_indices(ast_data, None, None, None, None, ctx, lang=lang)
    else:
        for node in ast_data.get("children", [ast_data]):
            if isinstance(node, dict):
                _visit_ast_for_indices(node, None, None, None, None, ctx, lang=lang)

    return (
        ctx.symbol_type_index,
        ctx.function_index,
        ctx.function_index_by_module,
        ctx.chunk_functions,
        ctx.chunk_function_calls,
        ctx.chunk_variables,
        ctx.ref_candidates_by_function,
        ctx.node_kinds,
    )


def build_function_facts_and_module_index(
    ast_path: str,
    ast_data: dict,
    lang: str,
    data_dir: Optional[str] = None,
) -> Tuple[List[FunctionFacts], Dict[str, Any], List[VariableFacts], Dict[str, List[str]]]:
    """한 AST 파일에서 FunctionFacts, module_index, VariableFacts 리스트 생성."""
    onto = get_ontology()
    module_types = onto.module_types(lang)
    function_types = onto.function_types(lang)
    variable_types = onto.variable_types(lang) | onto.constant_types(lang)

    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        dir_parts = parts[idx:-1]
        directory = "/".join(dir_parts) if dir_parts else ""
    else:
        directory = "/".join(p.parts[:-1]) if len(p.parts) > 1 else ""
    file_name = p.name  # AST JSON 파일명 (fallback)

    source_content = ""
    if data_dir:
        source_path = _source_path_for_ast(data_dir, ast_path, lang=lang)
        if source_path:
            # 원본 소스 경로로 directory/file_name 덮어쓰기
            sp = Path(source_path)
            src_parts = sp.parts
            if "source" in src_parts:
                idx = src_parts.index("source") + 1
                dir_parts = src_parts[idx:-1]
                directory = "/".join(dir_parts) if dir_parts else ""
            file_name = sp.name
            try:
                source_content = read_text_with_fallback(source_path)
            except Exception as e:
                logger.warning("[PHASE:1:AST] 소스 파일 읽기 실패 | path=%s error=%s", source_path, e)

    last_line = _last_line_from_ast(ast_data)
    processor = FrameworkAstProcessor(
        antlr_data=ast_data,
        directory=directory,
        file_name=file_name,
        last_line=last_line,
        source_content=source_content,
        lang=lang,
    )
    processor.build_static_graph_queries()

    nodes = processor._nodes
    classes = processor._unit_info  # Dict[str, ModuleInfo]  # noqa: F841

    function_facts_list: List[FunctionFacts] = []
    module_index: Dict[str, Any] = {}
    variable_facts_list: List[VariableFacts] = []
    node_kinds: Dict[str, str] = {}  # variable_id/function_id → AST 노드 타입 (서브레이블용)

    for node in nodes:
        if node.node_type in variable_types and node.name:
            # C 전역변수 등은 unit_key가 없을 수 있음 → 파일 경로 기반 fallback
            container_key = node.unit_key or f"{directory}/{file_name}"
            variable_id = f"{container_key}#{node.name}_{node.start_line}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            variable_facts_list.append(
                VariableFacts(
                    variable_id=variable_id,
                    module_key=container_key,
                    name=node.name,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    variable_type=getattr(node, "variable_type", None),
                    code_ref=code_ref,
                    comment=getattr(node, "comment", None),
                    annotations=getattr(node, "annotations", None),
                )
            )
            # variable_id → 원래 AST 노드 타입 저장 (서브레이블용)
            node_kinds[variable_id] = node.node_type
        if node.node_type in module_types and node.unit_name:
            # FILE 노드: unit_kind를 통해 kind를 "FILE"로 저장
            kind = node.unit_kind if node.unit_kind else node.node_type
            fqn = f"{node.package_name}.{node.unit_name}" if node.package_name else node.unit_name
            module_index[fqn] = {
                "module_key": node.unit_key,
                "fqn": fqn,
                "package_name": node.package_name,
                "name": node.unit_name,
                "kind": kind,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "comment": getattr(node, "comment", None),
                "annotations": getattr(node, "annotations", None),
                "extends_type": getattr(node, "extends_type", None),
                "implements_types": getattr(node, "implements_types", None),
            }

        if node.node_type in function_types and node.name:
            if node.package_name and node.unit_name:
                # OOP: package.Class.method / procedural: package.fileName.functionName
                fqn = f"{node.package_name}.{node.unit_name}.{node.name}"
            elif node.package_name:
                # fallback: package.functionName
                fqn = f"{node.package_name}.{node.name}"
            else:
                fqn = node.name
            function_id = fqn or f"{node.unit_key}#{node.name}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            function_facts_list.append(
                FunctionFacts(
                    function_id=function_id,
                    fqn=fqn,
                    signature=node.signature or "",
                    start_line=node.start_line,
                    end_line=node.end_line,
                    annotations=getattr(node, "annotations", None),
                    comment=getattr(node, "comment", None),
                    return_type=node.return_type,
                    params=node.parameters,
                    code_ref=code_ref,
                    module_key=node.unit_key,
                    package_name=node.package_name,
                    code=node.code,
                )
            )

    module_name_to_fqn: Dict[str, List[str]] = {}
    for fqn, info in module_index.items():
        if isinstance(info, dict):
            name = info.get("name")
            if name:
                module_name_to_fqn.setdefault(name, []).append(fqn)

    return function_facts_list, module_index, variable_facts_list, module_name_to_fqn, node_kinds


def _extract_package_fqns(module_index: Dict[str, Any]) -> List[str]:
    """module_index에서 고유 package_name 수집."""
    seen: set = set()
    out: List[str] = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        pkg = info.get("package_name")
        if pkg and pkg not in seen:
            seen.add(pkg)
            out.append(pkg)
    return sorted(out)


def _ast_json_path_for_source(data_dir: str, source_path: str, lang: str) -> Optional[str]:
    """source 파일 경로에 대응하는 target AST JSON 경로 (같은 상대경로, 확장자만 .json)."""
    onto = get_ontology()
    source_exts = onto.source_extensions(lang)
    p = Path(source_path)
    if p.suffix.lower() not in source_exts:
        return None
    source_dir = (Path(data_dir) / "source").resolve()
    try:
        rel = p.resolve().relative_to(source_dir)
    except ValueError:
        return None
    rel_json = str(Path(rel).with_suffix(".json")).replace("\\", "/")
    target_path = Path(data_dir) / "analysis" / "target" / rel_json
    return str(target_path) if target_path.is_file() else None


def _source_path_for_ast(data_dir: str, ast_path: str, lang: str) -> Optional[str]:
    """AST JSON 경로에 대응하는 원본 소스 파일 경로."""
    onto = get_ontology()
    source_exts = onto.source_extensions(lang)
    base = Path(data_dir)
    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        rel_parts = parts[idx:]
    else:
        rel_parts = p.parts
    rel_stem = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""
    stem_name = Path(rel_parts[-1]).stem if rel_parts else p.stem
    for ext in source_exts:
        rel_source = f"{rel_stem}/{stem_name}{ext}" if rel_stem else f"{stem_name}{ext}"
        candidates = [
            base / "source" / rel_source,
            base / rel_source,
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def extract_code_structure(
    source_ast_jobs: List[str],
    lang: str,
    data_dir: Optional[str] = None,
    run_dir: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """파일별로 AST 결과를 yield. 호출자가 누적 및 Neo4j 반영.

    Yields:
        Dict with keys: facts, cidx, fields, module_name_to_fqn,
        sym_idx, meth_idx, meth_by_cls, ref_cands, file_name
    """
    onto = get_ontology()
    source_exts = set(onto.source_extensions(lang))

    logger.info(
        "[PHASE:1:AST] AST 진입 | lang=%s paths=%d data_dir=%s run_dir=%s",
        lang,
        len(source_ast_jobs),
        data_dir or "",
        run_dir or "",
    )
    logger.info(
        "[PHASE:1:AST] [섹션] 파일별 처리: AST 파싱 → FunctionFacts, ChunkBundle 생성·저장 | 파일 %d개",
        len(source_ast_jobs),
    )

    source_dir: Optional[Path] = None
    if data_dir:
        source_dir = (Path(data_dir) / "source").resolve()

    for path in source_ast_jobs:
        try:
            ast_path_val: Optional[str] = None
            source_path_for_bundle: Optional[str] = None
            path_ext = Path(path).suffix.lower()
            if path_ext in source_exts and data_dir:
                ast_path_val = _ast_json_path_for_source(data_dir, path, lang=lang)
                if not ast_path_val:
                    logger.error("[PHASE:1:AST] AST JSON 없음, 스킵 | path=%s", path)
                    continue
                source_path_for_bundle = path
            else:
                ast_path_val = path
                source_path_for_bundle = _source_path_for_ast(data_dir, path, lang=lang) if data_dir else None

            ast_data = load_ast_json(ast_path_val)
            facts, cidx, fields, module_name_to_fqn, variable_node_kinds = build_function_facts_and_module_index(
                ast_path_val, ast_data, data_dir=data_dir, lang=lang,
            )

            (
                sym_idx,
                meth_idx,
                meth_by_cls,
                chunk_m,
                chunk_calls,
                chunk_vars,
                ref_cands,
                node_kinds,
            ) = collect_ast_indices_and_chunk_data(ast_data, lang=lang)
            # variable_id → node_type 매핑도 합침 (서브레이블용)
            node_kinds.update(variable_node_kinds)

            if data_dir and chunk_m and source_path_for_bundle:
                variable_chunk_input = [
                    {"variable_id": f.variable_id, "start_line": f.start_line, "end_line": f.end_line} for f in fields
                ]
                bundle = build_chunk_bundle_from_source(
                    source_path_for_bundle,
                    None,
                    chunk_m,
                    chunk_calls,
                    chunk_vars,
                    fields=variable_chunk_input,
                )
                if bundle:
                    p_for_key = Path(source_path_for_bundle)
                    rel_key: str
                    if source_dir:
                        try:
                            rel = p_for_key.resolve().relative_to(source_dir)
                            rel_key = "_".join(rel.parts)
                            for _ext in source_exts:
                                rel_key = rel_key.replace(_ext, "")
                        except ValueError:
                            rel_key = p_for_key.stem
                    else:
                        rel_key = p_for_key.stem
                    chunk_out = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
                    save_chunk_bundle(data_dir, bundle, rel_key, chunk_bundle_out_dir=chunk_out)
                    logger.debug("ChunkBundle 저장: %s", rel_key)
                    mid_to_code = {
                        m.function_id: (getattr(m, "function_code", None) or "")
                        for m in bundle.methods
                        if getattr(m, "function_code", None)
                    }
                    for f in facts:
                        if f.function_id in mid_to_code and mid_to_code[f.function_id]:
                            f.code = mid_to_code[f.function_id]
                    fid_to_code = {
                        fl.variable_id: (getattr(fl, "variable_code", None) or "")
                        for fl in bundle.member_vars
                        if getattr(fl, "variable_code", None)
                    }
                    for fl in fields:
                        if fl.variable_id in fid_to_code and fid_to_code[fl.variable_id]:
                            fl.code = fid_to_code[fl.variable_id]

            logger.info(
                "[PHASE:1:AST] 파일 처리 완료 | file=%s methods=%d classes=%d",
                Path(path).name,
                len(facts),
                len(cidx),
            )
            yield {
                "facts": facts,
                "cidx": cidx,
                "fields": fields,
                "module_name_to_fqn": module_name_to_fqn,
                "sym_idx": sym_idx,
                "meth_idx": meth_idx,
                "meth_by_cls": meth_by_cls,
                "ref_cands": ref_cands,
                "node_kinds": node_kinds,
                "file_name": Path(path).name,
            }
        except Exception as e:
            logger.error("[PHASE:1:AST] 처리 실패 | file=%s error=%s", Path(path).name, e, exc_info=True)
