"""Step 2: AST 순회 및 MethodFacts/ChunkBundle 생성 (범용 — Java/C/Python 등)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from analyzer.strategy.framework._ast_core import (
    FrameworkAstProcessor,
    CLASS_TYPES,
    FIELD_TYPES,
    METHOD_TYPES,
)
from analyzer.strategy.framework.common.ontology import get_ontology
from analyzer.strategy.framework.models import (
    FieldFacts,
    MethodFacts,
    NODE_VARIABLE,
    NODE_METHOD_CALL,
    NODE_NEW_INSTANCE,
)
from util.text_utils import read_text_with_fallback, read_json_with_fallback
from .chunk_bundle import (
    build_chunk_bundle_from_source,
    save_chunk_bundle,
    _get_chunk_bundle_dir_from_run_dir,
)

logger = logging.getLogger(__name__)


@dataclass
class AstVisitorContext:
    symbol_type_index: Dict[str, Dict[str, str]] = field(default_factory=dict)
    method_index: Dict[str, str] = field(default_factory=dict)
    method_index_by_class: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    field_types_by_class: Dict[str, Dict[str, str]] = field(default_factory=dict)
    chunk_methods: List[Dict[str, Any]] = field(default_factory=list)
    chunk_method_calls: List[Dict[str, Any]] = field(default_factory=list)
    chunk_variables: List[Dict[str, Any]] = field(default_factory=list)
    ref_candidates_by_method: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    method_call_seq: Dict[str, int] = field(default_factory=dict)


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
    class_name: Optional[str],
    class_fqn: Optional[str],
    method_id: Optional[str],
    ctx: AstVisitorContext,
    lang: str = "java",
) -> None:
    """Raw AST 순회: symbol_type_index, method_index, ChunkBundle용 리스트 수집."""
    onto = get_ontology()
    container_types = onto.container_types(lang)
    method_types = onto.method_types(lang)
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

    if node_type in container_types:
        class_name = node.get("name") or class_name
        if package_name and class_name:
            class_fqn = f"{package_name}.{class_name}"
        else:
            class_fqn = class_name

    if node_type in method_types:
        method_name = node.get("name", "")
        if class_fqn and method_name:
            method_id = f"{class_fqn}.{method_name}"
        elif package_name and method_name:
            # No-container (C FUNCTION 등): package.functionName
            method_id = f"{package_name}.{method_name}"
        else:
            method_id = method_id or f"unknown_{start_line}"
        if method_id and method_id not in ctx.method_index:
            ctx.method_index[method_id] = method_id
        if class_fqn and method_name:
            ctx.method_index_by_class.setdefault(class_fqn, {}).setdefault(method_name, []).append(method_id)
        ctx.chunk_methods.append({"method_id": method_id, "start_line": start_line, "end_line": end_line})

    for ch in children:
        ch_type = ch.get("type", "")
        ch_start = ch.get("startLine", 0)
        ch_end = ch.get("endLine", 0)

        if ch_type == NODE_VARIABLE and method_id:
            var_name = ch.get("name", "")
            var_type = ch.get("fieldType")
            if ch.get("initializerContainsNewInstance"):
                for sub in ch.get("children", []):
                    if isinstance(sub, dict) and sub.get("type") == NODE_NEW_INSTANCE:
                        var_type = sub.get("name") or var_type
                        break
            if var_name:
                ctx.symbol_type_index.setdefault(method_id, {})[var_name] = var_type or ""
            flags: Dict[str, Any] = {}
            if ch.get("initializerContainsMethodCall"):
                flags["initializerContainsMethodCall"] = True
            if ch.get("initializerContainsNewInstance"):
                flags["initializerContainsNewInstance"] = True
            ctx.chunk_variables.append(
                {
                    "parent_method_id": method_id,
                    "var_name": var_name,
                    "var_type": var_type,
                    "start_line": ch_start,
                    "end_line": ch_end,
                    "flags": flags if flags else None,
                }
            )

        # GLOBAL_VARIABLE: 함수 밖 변수 → __global__ 키에 수집
        if ch_type == NODE_VARIABLE and not method_id:
            var_name = ch.get("name", "")
            var_type = ch.get("fieldType")
            if var_name:
                ctx.symbol_type_index.setdefault("__global__", {})[var_name] = var_type or ""

        if ch_type in call_types and method_id:
            seq = ctx.method_call_seq.get(method_id, 0)
            call_id = f"C:{ch_start}:{ch_end}:{seq}"
            ctx.method_call_seq[method_id] = seq + 1
            ctx.chunk_method_calls.append(
                {
                    "parent_method_id": method_id,
                    "call_id": call_id,
                    "start_line": ch_start,
                    "end_line": ch_end,
                }
            )
            ctx.ref_candidates_by_method.setdefault(method_id, []).append(
                {
                    "kind": "CALL_REF",
                    "receiver": None,
                    "method_name": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        if ch_type == NODE_NEW_INSTANCE and method_id:
            ctx.ref_candidates_by_method.setdefault(method_id, []).append(
                {
                    "kind": "NEW_INSTANCE_REF",
                    "className": ch.get("name"),
                    "line_range": (ch_start, ch_end),
                }
            )

        _visit_ast_for_indices(ch, package_name, class_name, class_fqn, method_id, ctx, lang=lang)


def collect_ast_indices_and_chunk_data(
    ast_data: dict,
    lang: str = "java",
) -> Tuple[
    Dict[str, Dict[str, str]],
    Dict[str, str],
    Dict[str, Dict[str, List[str]]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, List[Dict[str, Any]]],
]:
    """AST에서 symbol_type_index, method_index, method_index_by_class, ChunkBundle용 methods/calls/variables, ref_candidates_by_method 반환."""
    ctx = AstVisitorContext()

    if ast_data.get("type") == "FILE":
        _visit_ast_for_indices(ast_data, None, None, None, None, ctx, lang=lang)
    else:
        for node in ast_data.get("children", [ast_data]):
            if isinstance(node, dict):
                _visit_ast_for_indices(node, None, None, None, None, ctx, lang=lang)

    return (
        ctx.symbol_type_index,
        ctx.method_index,
        ctx.method_index_by_class,
        ctx.chunk_methods,
        ctx.chunk_method_calls,
        ctx.chunk_variables,
        ctx.ref_candidates_by_method,
    )


def build_method_facts_and_class_index(
    ast_path: str,
    ast_data: dict,
    data_dir: Optional[str] = None,
    lang: str = "java",
) -> Tuple[List[MethodFacts], Dict[str, Any], List[FieldFacts], Dict[str, List[str]]]:
    """한 AST 파일에서 MethodFacts, class_index, FieldFacts 리스트 생성."""
    onto = get_ontology()
    container_types = onto.container_types(lang)
    method_types = onto.method_types(lang)
    field_types = onto.field_types(lang)

    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        dir_parts = parts[idx:-1]
        directory = "/".join(dir_parts) if dir_parts else ""
    else:
        directory = "/".join(p.parts[:-1]) if len(p.parts) > 1 else ""
    file_name = p.name

    source_content = ""
    if data_dir:
        source_path = _source_path_for_ast(data_dir, ast_path, lang=lang)
        if source_path:
            try:
                source_content = read_text_with_fallback(source_path)
            except Exception:
                pass

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
    classes = processor._unit_info  # Dict[str, ClassInfo]  # noqa: F841

    method_facts_list: List[MethodFacts] = []
    class_index: Dict[str, Any] = {}
    field_facts_list: List[FieldFacts] = []

    for node in nodes:
        if node.node_type in field_types and node.unit_key and node.name:
            field_id = f"{node.unit_key}#{node.name}_{node.start_line}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            field_facts_list.append(
                FieldFacts(
                    field_id=field_id,
                    class_key=node.unit_key,
                    name=node.name,
                    start_line=node.start_line,
                    end_line=node.end_line,
                    field_type=getattr(node, "field_type", None),
                    code_ref=code_ref,
                    comment=getattr(node, "comment", None),
                    annotations=getattr(node, "annotations", None),
                )
            )
        if node.node_type in container_types and node.unit_name:
            fqn = f"{node.package_name}.{node.unit_name}" if node.package_name else node.unit_name
            class_index[fqn] = {
                "class_key": node.unit_key,
                "fqn": fqn,
                "package_name": node.package_name,
                "name": node.unit_name,
                "kind": node.node_type,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "comment": getattr(node, "comment", None),
                "annotations": getattr(node, "annotations", None),
                "extends_type": getattr(node, "extends_type", None),
                "implements_types": getattr(node, "implements_types", None),
            }
            if node.package_name:
                class_index[node.unit_name] = class_index[fqn]

        if node.node_type in method_types and node.name:
            if node.package_name and node.unit_name:
                # Java: package.Class.method
                fqn = f"{node.package_name}.{node.unit_name}.{node.name}"
            elif node.package_name:
                # No-container (C FUNCTION 등): package.functionName
                fqn = f"{node.package_name}.{node.name}"
            else:
                fqn = node.name
            method_id = fqn or f"{node.unit_key}#{node.name}"
            code_ref = f"{directory}/{file_name}:{node.start_line}-{node.end_line}"
            method_facts_list.append(
                MethodFacts(
                    method_id=method_id,
                    fqn=fqn,
                    signature=node.signature or "",
                    start_line=node.start_line,
                    end_line=node.end_line,
                    annotations=getattr(node, "annotations", None),
                    comment=getattr(node, "comment", None),
                    return_type=node.return_type,
                    params=node.parameters,
                    code_ref=code_ref,
                    class_key=node.unit_key,
                    package_name=node.package_name,
                    code=node.code,
                )
            )

    class_index_simple: Dict[str, List[str]] = {}
    for fqn, info in class_index.items():
        if isinstance(info, dict):
            name = info.get("name")
            if name:
                class_index_simple.setdefault(name, []).append(fqn)

    return method_facts_list, class_index, field_facts_list, class_index_simple


def _extract_package_fqns(class_index: Dict[str, Any]) -> List[str]:
    """class_index에서 고유 package_name 수집."""
    seen: set = set()
    out: List[str] = []
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        pkg = info.get("package_name")
        if pkg and pkg not in seen:
            seen.add(pkg)
            out.append(pkg)
    return sorted(out)


def _ast_json_path_for_source(data_dir: str, source_path: str, lang: str = "java") -> Optional[str]:
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


# 하위호환 alias
_ast_json_path_for_java = _ast_json_path_for_source


def _source_path_for_ast(data_dir: str, ast_path: str, lang: str = "java") -> Optional[str]:
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


def run_step2(
    source_ast_jobs: List[str],
    data_dir: Optional[str] = None,
    run_dir: Optional[str] = None,
    lang: str = "java",
) -> Generator[Dict[str, Any], None, None]:
    """파일별로 AST 결과를 yield. 호출자가 누적 및 Neo4j 반영.

    Yields:
        Dict with keys: facts, cidx, fields, class_index_simple,
        sym_idx, meth_idx, meth_by_cls, ref_cands, file_name
    """
    onto = get_ontology()
    source_exts = set(onto.source_extensions(lang))

    logger.info(
        "[FW:STEP2] AST 진입 | lang=%s paths=%d data_dir=%s run_dir=%s",
        lang,
        len(source_ast_jobs),
        data_dir or "",
        run_dir or "",
    )
    logger.info(
        "[FW:STEP2] [섹션] 파일별 처리: AST 파싱 → MethodFacts, ChunkBundle 생성·저장 | 파일 %d개",
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
                    logger.warning("AST JSON 없음, 스킵 | java=%s", path)
                    continue
                source_path_for_bundle = path
            else:
                ast_path_val = path
                source_path_for_bundle = _source_path_for_ast(data_dir, path, lang=lang) if data_dir else None

            ast_data = load_ast_json(ast_path_val)
            facts, cidx, fields, class_index_simple = build_method_facts_and_class_index(
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
            ) = collect_ast_indices_and_chunk_data(ast_data, lang=lang)

            if data_dir and chunk_m and source_path_for_bundle:
                field_chunk_input = [
                    {"field_id": f.field_id, "start_line": f.start_line, "end_line": f.end_line} for f in fields
                ]
                bundle = build_chunk_bundle_from_source(
                    source_path_for_bundle,
                    None,
                    chunk_m,
                    chunk_calls,
                    chunk_vars,
                    fields=field_chunk_input,
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
                        m.method_id: (getattr(m, "method_code", None) or "")
                        for m in bundle.methods
                        if getattr(m, "method_code", None)
                    }
                    for f in facts:
                        if f.method_id in mid_to_code and mid_to_code[f.method_id]:
                            f.code = mid_to_code[f.method_id]
                    fid_to_code = {
                        fl.field_id: (getattr(fl, "field_code", None) or "")
                        for fl in bundle.fields
                        if getattr(fl, "field_code", None)
                    }
                    for fl in fields:
                        if fl.field_id in fid_to_code and fid_to_code[fl.field_id]:
                            fl.code = fid_to_code[fl.field_id]

            logger.info(
                "[FW:STEP2] 파일 처리 완료 | file=%s methods=%d classes=%d",
                Path(path).name,
                len(facts),
                len(cidx),
            )
            yield {
                "facts": facts,
                "cidx": cidx,
                "fields": fields,
                "class_index_simple": class_index_simple,
                "sym_idx": sym_idx,
                "meth_idx": meth_idx,
                "meth_by_cls": meth_by_cls,
                "ref_cands": ref_cands,
                "file_name": Path(path).name,
            }
        except Exception as e:
            logger.warning("AST 처리 스킵 %s: %s", Path(path).name, e)
