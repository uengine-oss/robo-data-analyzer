"""ChunkBundle I/O: 생성, 직렬화, 로드, 검색."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from util.text_utils import (
    add_line_numbers,
    slice_by_line_range,
)
from analyzer.strategy.framework.models import (
    ChunkBundle,
    ChunkBundleFile,
    ChunkBundleMemberVar,
    ChunkBundleMethod,
    ChunkBundleFunctionCall,
    ChunkBundleLocalVar,
)

logger = logging.getLogger(__name__)


def _span_index_key(file_path: str, start_line: int, end_line: int) -> str:
    """LangSmith 직렬화용: tuple 대신 문자열 키 사용."""
    return repr((file_path, start_line, end_line))


def _span_index_key_parse(key: str) -> Optional[Tuple[str, int, int]]:
    """문자열 키를 (file_path, start, end)로 복원."""
    try:
        t = ast.literal_eval(key)
        if isinstance(t, (list, tuple)) and len(t) == 3:
            return (str(t[0]), int(t[1]), int(t[2]))
    except (ValueError, SyntaxError, TypeError):
        pass
    return None

def _get_chunk_bundle_dir(data_dir=None):
    base = data_dir or settings.path.data_dir
    return str(Path(base) / "chunk_bundles")

def _get_chunk_bundle_dir_from_run_dir(run_dir):
    return str(Path(run_dir) / "chunk_bundles")


def _file_hash(path: Path, encoding: str = "utf-8") -> Optional[str]:
    try:
        raw = path.read_bytes()
        return hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        return None


def build_chunk_bundle_from_source(
    source_path: str | Path,
    encoding: Optional[str],
    methods: List[Dict[str, Any]],  # function_id, start_line, end_line
    function_calls: List[Dict[str, Any]],  # parent_function_id, call_id, start_line, end_line
    variables: List[Dict[str, Any]],  # parent_function_id, var_name, var_type, start_line, end_line, flags
    fields: Optional[List[Dict[str, Any]]] = None,  # variable_id, start_line, end_line
) -> Optional[ChunkBundle]:
    """원본 소스를 slice하여 function_code/call_code를 채운 ChunkBundle 생성.

    analysis JSON이 있는 파일에 대해서만 호출. source_path에 해당 파일이 없으면 None.
    """
    path = Path(source_path)
    if not path.is_file():
        logger.info("[FW:CHUNK] 번들 스킵 | reason=not_file file=%s", path.name)
        logger.debug("ChunkBundle 스킵 (소스 없음): %s", path.name)
        return None

    try:
        enc = encoding or "utf-8"
        raw = path.read_text(encoding=enc)
        text_with_lines = add_line_numbers(raw)
    except Exception as e:
        logger.warning("ChunkBundle 소스 읽기 실패 %s: %s", path, e)
        return None

    file_hash_val = _file_hash(path, encoding or "utf-8")
    file_meta = ChunkBundleFile(
        file_path=str(path),
        file_hash=file_hash_val,
        encoding=encoding,
    )

    bundle_methods: List[ChunkBundleMethod] = []
    for m in methods:
        start_line = int(m.get("start_line", 0))
        end_line = int(m.get("end_line", 0))
        function_code = slice_by_line_range(text_with_lines, start_line, end_line) if end_line >= start_line else None
        bundle_methods.append(
            ChunkBundleMethod(
                function_id=m["function_id"],
                start_line=start_line,
                end_line=end_line,
                function_code=function_code,
            )
        )

    bundle_calls: List[ChunkBundleFunctionCall] = []
    for c in function_calls:
        start_line = int(c.get("start_line", 0))
        end_line = int(c.get("end_line", 0))
        call_code = slice_by_line_range(text_with_lines, start_line, end_line) if end_line >= start_line else ""
        bundle_calls.append(
            ChunkBundleFunctionCall(
                parent_function_id=c["parent_function_id"],
                call_id=c["call_id"],
                start_line=start_line,
                end_line=end_line,
                call_code=call_code,
            )
        )

    bundle_local_vars: List[ChunkBundleLocalVar] = []
    for v in variables:
        start_line = int(v.get("start_line", 0))
        end_line = int(v.get("end_line", 0))
        flags = v.get("flags")
        initializer_code = None
        if flags and flags.get("initializerContainsMethodCall") and end_line >= start_line:
            initializer_code = slice_by_line_range(text_with_lines, start_line, end_line)
        bundle_local_vars.append(
            ChunkBundleLocalVar(
                parent_function_id=v["parent_function_id"],
                var_name=v["var_name"],
                var_type=v.get("var_type"),
                start_line=start_line,
                end_line=end_line,
                flags=flags,
                initializer_code=initializer_code,
            )
        )

    bundle_member_vars: List[ChunkBundleMemberVar] = []
    for f in fields or []:
        start_line = int(f.get("start_line", 0))
        end_line = int(f.get("end_line", 0))
        variable_code = slice_by_line_range(text_with_lines, start_line, end_line) if end_line >= start_line else None
        bundle_member_vars.append(
            ChunkBundleMemberVar(
                variable_id=f["variable_id"],
                start_line=start_line,
                end_line=end_line,
                variable_code=variable_code,
            )
        )

    out = ChunkBundle(
        file_meta=file_meta,
        methods=bundle_methods,
        function_calls=bundle_calls,
        local_vars=bundle_local_vars,
        member_vars=bundle_member_vars,
    )
    logger.info(
        "[FW:CHUNK] 번들 생성 완료 | file=%s methods=%d function_calls=%d local_vars=%d member_vars=%d",
        path.name,
        len(bundle_methods),
        len(bundle_calls),
        len(bundle_local_vars),
        len(bundle_member_vars),
    )
    return out


def _bundle_to_dict(bundle: ChunkBundle) -> Dict[str, Any]:
    """ChunkBundle을 JSON 직렬화 가능한 dict로."""
    from dataclasses import asdict
    return {
        "file_meta": asdict(bundle.file_meta),
        "methods": [asdict(m) for m in bundle.methods],
        "function_calls": [asdict(c) for c in bundle.function_calls],
        "local_vars": [asdict(v) for v in bundle.local_vars],
        "member_vars": [asdict(f) for f in bundle.member_vars],
    }


def _dict_to_bundle(data: Dict[str, Any]) -> ChunkBundle:
    """dict에서 ChunkBundle 복원."""
    def local_var_from_dict(v: Dict[str, Any]) -> ChunkBundleLocalVar:
        return ChunkBundleLocalVar(
            parent_function_id=v["parent_function_id"],
            var_name=v["var_name"],
            var_type=v.get("var_type"),
            start_line=int(v.get("start_line", 0)),
            end_line=int(v.get("end_line", 0)),
            flags=v.get("flags"),
            initializer_code=v.get("initializer_code"),
        )
    member_vars_data = data.get("member_vars") or data.get("fields") or []
    return ChunkBundle(
        file_meta=ChunkBundleFile(**data["file_meta"]),
        methods=[ChunkBundleMethod(**m) for m in data["methods"]],
        function_calls=[ChunkBundleFunctionCall(**c) for c in data.get("function_calls") or data.get("method_calls") or []],
        local_vars=[local_var_from_dict(v) for v in data.get("local_vars") or data.get("variables") or []],
        member_vars=[ChunkBundleMemberVar(**f) for f in member_vars_data],
    )


def save_chunk_bundle(
    data_dir: str,
    bundle: ChunkBundle,
    relative_key: str,
    chunk_bundle_out_dir: Optional[str] = None,
) -> str:
    """ChunkBundle을 저장. chunk_bundle_out_dir 있으면 그 경로(run_dir/chunk_bundles), 없으면 data_dir/chunk_bundles.

    relative_key는 경로 구분자를 _로 치환한 식별자 (예: com_example_Foo).
    Returns: 저장된 파일 경로.
    """
    out_dir = Path(chunk_bundle_out_dir) if chunk_bundle_out_dir else Path(_get_chunk_bundle_dir(data_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_key = relative_key.replace("/", "_").replace("\\", "_").strip("_")
    if not safe_key.endswith(".json"):
        safe_key = f"{safe_key}.json"
    out_path = out_dir / safe_key
    out_path.write_text(
        json.dumps(_bundle_to_dict(bundle), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(out_path)


def load_chunk_bundle(path: str | Path) -> ChunkBundle:
    """ChunkBundle JSON 파일에서 로드."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _dict_to_bundle(data)


def load_field_chunks_from_bundles(
    data_dir: str = "",
    chunk_bundle_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """모든 ChunkBundle에서 VARIABLE 청크 수집. link_chunks_to_indexes용 {variable_id, code, code_ref, line_range} 리스트."""
    base = Path(chunk_bundle_dir) if chunk_bundle_dir else Path(_get_chunk_bundle_dir(data_dir))
    out: List[Dict[str, Any]] = []
    if not base.is_dir():
        return out
    for path in base.glob("*.json"):
        try:
            bundle = load_chunk_bundle(path)
            fp = bundle.file_meta.file_path
            for f in getattr(bundle, "fields", []):
                if getattr(f, "variable_code", None):
                    out.append({
                        "variable_id": f.variable_id,
                        "code": f.variable_code,
                        "code_ref": fp,
                        "line_range": (f.start_line, f.end_line),
                    })
        except Exception as e:
            logger.debug("ChunkBundle 필드 로드 스킵 %s: %s", path.name, e)
    return out


def chunk_bundle_path_for_ast_path(data_dir: str, ast_path: str) -> str:
    """AST JSON 경로에서 대응하는 ChunkBundle 파일 경로 유도.

    예: data/analysis/target/com/example/Foo.json -> chunk_bundles/com_example_Foo.json
    """
    p = Path(ast_path)
    parts = p.parts
    if "analysis" in parts and "target" in parts:
        idx = parts.index("target") + 1
        rel = parts[idx:]
    else:
        rel = p.parts
    # .json -> 동일 base로 chunk bundle
    stem = "_".join(rel[:-1] + (p.stem,)) if len(rel) > 1 else p.stem
    return str(Path(_get_chunk_bundle_dir(data_dir)) / f"{stem}.json")


def build_bundle_span_index(
    data_dir: Optional[str] = None,
    chunk_bundle_dir: Optional[str] = None,
    artifact_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """ChunkBundle 전체 로드 후 span_index(문자열 키), chunk_index 반환.

    artifact_index, sql_index가 전달되면 해당 청크도 chunk_index에 통합하여
    bundle_search()로 소스코드·XML·SQL 통합 검색이 가능하도록 한다.
    """
    base = Path(chunk_bundle_dir) if chunk_bundle_dir else Path(_get_chunk_bundle_dir(data_dir or ""))
    span_index: Dict[str, Dict[str, Any]] = {}
    chunk_index: Dict[str, Dict[str, Any]] = {}
    if not base.is_dir():
        logger.info("[FW:CHUNK] span_index 스킵 | reason=dir_not_found dir=%s", base.name)
    else:
        for path in base.glob("*.json"):
            try:
                bundle = load_chunk_bundle(path)
                fp = bundle.file_meta.file_path
                for m in bundle.methods:
                    if m.function_code:
                        key = _span_index_key(fp, m.start_line, m.end_line)
                        cid = m.function_id
                        span_index[key] = {"chunk_id": cid, "text": m.function_code, "filePath": fp, "span": [m.start_line, m.end_line]}
                        chunk_index[cid] = {"filePath": fp, "span": [m.start_line, m.end_line], "text": m.function_code}
                for c in bundle.function_calls:
                    if c.call_code:
                        key = _span_index_key(fp, c.start_line, c.end_line)
                        cid = c.call_id
                        span_index[key] = {"chunk_id": cid, "text": c.call_code, "filePath": fp, "span": [c.start_line, c.end_line]}
                        chunk_index[cid] = {"filePath": fp, "span": [c.start_line, c.end_line], "text": c.call_code}
                for v in bundle.local_vars:
                    if v.initializer_code:
                        key = _span_index_key(fp, v.start_line, v.end_line)
                        cid = f"C:{v.start_line}:{v.end_line}:var"
                        span_index[key] = {"chunk_id": cid, "text": v.initializer_code, "filePath": fp, "span": [v.start_line, v.end_line]}
                        chunk_index[cid] = {"filePath": fp, "span": [v.start_line, v.end_line], "text": v.initializer_code}
                for f in getattr(bundle, "fields", []):
                    if f.variable_code:
                        key = _span_index_key(fp, f.start_line, f.end_line)
                        span_index[key] = {"chunk_id": f.variable_id, "text": f.variable_code, "filePath": fp, "span": [f.start_line, f.end_line]}
                        chunk_index[f.variable_id] = {"filePath": fp, "span": [f.start_line, f.end_line], "text": f.variable_code}
            except Exception as e:
                logger.debug("ChunkBundle 인덱스 스킵 %s: %s", path.name, e)

    # artifact 청크 (XML entity 정의, EJB 매핑 등) 통합
    art_count = 0
    for cid, ac in (artifact_index or {}).items():
        text = (getattr(ac, "text", "") or "").strip()
        if not text:
            continue
        fp = getattr(ac, "source_path", "")
        sl = getattr(ac, "start_line", 0)
        el = getattr(ac, "end_line", 0)
        chunk_index[cid] = {"filePath": fp, "span": [sl, el], "text": text, "chunk_type": "artifact"}
        art_count += 1

    # SQL 청크 (SQL 매핑 XML의 SQL문) 통합
    sql_count = 0
    for sql_key, stmt in (sql_index or {}).items():
        text = (getattr(stmt, "sql_text", "") or "").strip()
        if not text:
            continue
        fp = getattr(stmt, "file_path", "")
        sl = getattr(stmt, "start_line", 0)
        el = getattr(stmt, "end_line", 0)
        chunk_index[sql_key] = {"filePath": fp, "span": [sl, el], "text": text, "chunk_type": "sql"}
        sql_count += 1

    logger.info(
        "[FW:CHUNK] span_index 완료 | span_count=%d chunk_count=%d (artifact=%d sql=%d)",
        len(span_index), len(chunk_index), art_count, sql_count,
    )
    return span_index, chunk_index


def bundle_search(
    data_dir: str,
    query: str,
    kind: str = "text",
    span_index: Optional[Dict[str, Dict[str, Any]]] = None,
    chunk_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """번들에서 query 검색. kind: symbol|text. 반환: [{chunk_id, filePath, span, score}]."""
    if span_index is None or chunk_index is None:
        span_index, chunk_index = build_bundle_span_index(data_dir)
    results: List[Dict[str, Any]] = []
    query_lower = (query or "").strip().lower()
    if not query_lower:
        return results
    for cid, info in chunk_index.items():
        text = (info.get("text") or "")
        if not text:
            continue
        if kind == "symbol":
            if query_lower in text.lower() or (query or "").strip() in text:
                results.append({
                    "chunk_id": cid,
                    "filePath": info.get("filePath", ""),
                    "span": info.get("span", [0, 0]),
                    "score": 0.9,
                })
        else:
            if query_lower in text.lower():
                results.append({
                    "chunk_id": cid,
                    "filePath": info.get("filePath", ""),
                    "span": info.get("span", [0, 0]),
                    "score": 0.85,
                })
    return results[:50]


def bundle_get_span(
    file_path: str,
    start_line: int,
    end_line: int,
    data_dir: Optional[str] = None,
    span_index: Optional[Dict[str, Dict[str, Any]]] = None,
    chunk_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """(file_path, start_line, end_line)에 해당하는 번들 snippet 반환. 파일 읽기 없음."""
    if span_index is None or chunk_index is None:
        if data_dir is None:
            return None
        span_index, chunk_index = build_bundle_span_index(data_dir)
    key = _span_index_key(file_path, start_line, end_line)
    hit = span_index.get(key)
    if hit:
        return {"text": hit.get("text", ""), "filePath": file_path, "span": [start_line, end_line]}
    for k, hit in span_index.items():
        parsed = _span_index_key_parse(k)
        if not parsed:
            continue
        fp, s, e = parsed
        if fp == file_path and s <= start_line and e >= end_line:
            return {"text": hit.get("text", ""), "filePath": fp, "span": [s, e]}
    return None


def bundle_get_chunk(
    chunk_id: str,
    data_dir: Optional[str] = None,
    chunk_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """chunk_id로 번들 청크 조회. 반환: {text, filePath, span}."""
    if chunk_index is None:
        if data_dir is None:
            return None
        _, chunk_index = build_bundle_span_index(data_dir)
    info = chunk_index.get(chunk_id)
    if not info:
        return None
    return {"text": info.get("text", ""), "filePath": info.get("filePath", ""), "span": info.get("span", [0, 0])}
