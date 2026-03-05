"""Step 6: 역할 판단 + 요약. 결정론 규칙 우선, LLM 선택. use_llm=True 시 analysis.yaml 스타일로 summary + calls + dependencies 한 번에 수신."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from exp.models import FieldFacts, MethodFacts, ReferToEdge, ReadsWritesEdge
from util.rule_loader import RuleLoader
from util.text_utils import add_line_numbers, slice_by_line_range

logger = logging.getLogger(__name__)


def compute_stereotype(
    method_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
) -> str:
    """쓰기(WRITES/INSERT/UPDATE/DELETE) 존재 → command, 조회만(상태 변화 없음) → readmodel. command/readmodel 두 가지로 결정."""
    sql_keys = {e.target_id for e in refer_to_edges if e.method_id == method_id and e.target_type == "SqlStatement"}
    writes = any(
        rw.sql_key in sql_keys and rw.relation in ("WRITES", "INSERT", "UPDATE", "DELETE")
        for rw in reads_writes
    )
    if writes:
        return "command"
    return "readmodel"


def _get_class_context(class_index: Dict[str, Any], class_key: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """class_index에서 class_key에 해당하는 comment, annotations 반환."""
    if not class_index or not class_key:
        return None, None
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        if info.get("class_key") == class_key:
            return info.get("comment"), info.get("annotations")
    return None, None


def _build_linked_sql_context(
    method_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    sql_text_max_len: int = 800,
) -> str:
    """메서드가 참조하는 SQL 노드의 내용(SQL 일부 + 테이블·relation)을 하나의 문자열로 구성. LLM context용."""
    sql_keys = {e.target_id for e in refer_to_edges if e.method_id == method_id and e.target_type == "SqlStatement"}
    if not sql_keys:
        return ""
    parts: List[str] = []
    for sql_key in sorted(sql_keys):
        chunk = (sql_index or {}).get(sql_key)
        sql_snippet = ""
        if chunk is not None:
            if isinstance(chunk, dict):
                sql_snippet = (chunk.get("sql_text") or "")[:sql_text_max_len]
            else:
                sql_snippet = (getattr(chunk, "sql_text", "") or "")[:sql_text_max_len]
        rw_list = [f"{rw.table_name}({rw.relation})" for rw in reads_writes if rw.sql_key == sql_key]
        tables_ops = ", ".join(rw_list[:30]) if rw_list else "(없음)"
        block = f"sql_key: {sql_key} | SQL 일부: {sql_snippet} | 테이블·연산: {tables_ops}"
        parts.append(block)
    if not parts:
        return ""
    return "[연결된 SQL] " + " || ".join(parts)


# 연결 아키텍처 컨텍스트 전체 상한 (문자 수). 0이면 제한 없음.
# 8000자 ≈ 2000토큰 (estimate_tokens = len/4 기준). 과도한 context로 인한 지연·비용을 막기 위한 상한.
LINKED_ARCHITECTURE_CONTEXT_MAX = int(os.environ.get("EXP_STEP6_CONTEXT_MAX", "8000"))


def _build_linked_architecture_context(
    method_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    class_index: Optional[Dict[str, Any]] = None,
    sql_text_max_len: int = 800,
    config_snippet_max_len: int = 500,
    class_info_max_len: int = 300,
    total_max_len: int = 0,
) -> str:
    """메서드가 참조하는 모든 아키텍처 노드(SQL, Config, Class)의 내용을 하나의 문자열로 구성. LLM context용."""
    parts: List[str] = []
    # 1) 연결된 SQL
    sql_ctx = _build_linked_sql_context(method_id, refer_to_edges, reads_writes, sql_index, sql_text_max_len)
    if sql_ctx:
        parts.append(sql_ctx)
    # 2) 연결된 Config
    config_keys = {e.target_id for e in refer_to_edges if e.method_id == method_id and e.target_type == "ConfigSection"}
    for ckey in sorted(config_keys):
        chunk = (config_index or {}).get(ckey)
        snippet = ""
        if chunk is not None:
            if isinstance(chunk, dict):
                snippet = (chunk.get("raw_snippet") or "")[:config_snippet_max_len]
            else:
                snippet = (getattr(chunk, "raw_snippet", "") or "")[:config_snippet_max_len]
        parts.append(f"[연결된 Config] config_key: {ckey} | 내용: {snippet}")
    # 3) 연결된 Class (참조하는 다른 클래스; 소속 클래스 제외)
    class_ids = {e.target_id for e in refer_to_edges if e.method_id == method_id and e.target_type == "Class"}
    for cid in sorted(class_ids):
        info = (class_index or {}).get(cid)
        if not info and class_index:
            for k, v in class_index.items():
                if not isinstance(v, dict):
                    continue
                if v.get("fqn") == cid or v.get("class_key") == cid or k == cid:
                    info = v
                    break
        desc = ""
        if isinstance(info, dict):
            name = info.get("name") or info.get("class_key") or cid
            comment = (info.get("comment") or "")[:class_info_max_len]
            desc = f"name: {name}" + (f" | comment: {comment}" if comment else "")
        else:
            desc = str(cid)
        parts.append(f"[연결된 Class] target_id: {cid} | {desc}")
    out = " ".join(parts) if parts else ""
    if total_max_len > 0 and len(out) > total_max_len:
        out = out[: total_max_len - 20] + " ... [truncated]"
    return out


def _invoke_analysis_llm(
    m: MethodFacts,
    api_key: str,
    locale: str = "ko",
    calls_outgoing: Optional[List[str]] = None,
    reads_writes_summary: Optional[str] = None,
    class_comment: Optional[str] = None,
    class_annotations: Optional[str] = None,
    linked_architecture_context: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """메서드 1개에 대해 rules/framework/analysis 규칙 실행 → analysis[0] (summary, calls, dependencies, stereotype) 반환. 실패 시 None.
    linked_architecture_context: 이 메서드가 참조하는 아키텍처 노드(SQL, Config, Class) 내용. LLM이 summary·stereotype 판단에 활용."""
    if not api_key or not (m.code or "").strip():
        reason = "no_api_key" if not api_key else "no_code"
        logger.info("[EXP:STEP6] LLM 스킵 | method_id=%s reason=%s", m.method_id, reason)
        return None
    code = m.code.strip()
    ranges = [{"startLine": m.start_line, "endLine": m.end_line}]
    count = 1
    context_parts: List[str] = []
    if class_comment:
        context_parts.append("class_comment: " + (class_comment[:500] if len(class_comment or "") > 500 else (class_comment or "")))
    if class_annotations:
        context_parts.append("class_annotations: " + (class_annotations[:300] if len(class_annotations or "") > 300 else (class_annotations or "")))
    if calls_outgoing:
        context_parts.append("calls_outgoing: " + ", ".join(calls_outgoing[:20]))
    if reads_writes_summary:
        context_parts.append("reads_writes: " + reads_writes_summary)
    if linked_architecture_context and linked_architecture_context.strip():
        context_parts.append(linked_architecture_context.strip())
    context = "; ".join(context_parts)
    inputs: Dict[str, Any] = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    try:
        loader = RuleLoader(target_lang="framework")
        result = loader.execute("analysis", inputs, api_key)
        analysis_list = result.get("analysis") if isinstance(result, dict) else None
        if not analysis_list or len(analysis_list) < 1:
            logger.info("[EXP:STEP6] LLM 스킵 | method_id=%s reason=empty_analysis_result", m.method_id)
            return None
        out = analysis_list[0]
        logger.info(
            "[EXP:STEP6] LLM 분석 완료 | method_id=%s has_summary=%s calls=%d dependencies=%d",
            m.method_id,
            bool(out.get("summary")),
            len(out.get("calls") or []),
            len(out.get("dependencies") or []),
        )
        return out
    except Exception as e:
        logger.warning("[EXP:STEP6] LLM 분석 실패 method_id=%s error=%s", m.method_id, e)
        return None


def _process_one_method_for_analysis(
    m: MethodFacts,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    api_key: str,
    locale: str,
    class_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """단일 메서드에 대해 stereotype + (summary, calls, dependencies). 병렬 호출용. LLM이 stereotype 반환하면 사용, 없으면 compute_stereotype fallback."""
    fallback_stereotype = compute_stereotype(m.method_id, refer_to_edges, reads_writes)
    summary = f"{m.fqn} ({fallback_stereotype}); lines {m.start_line}-{m.end_line}"
    calls_outgoing = [f"{e.target_type}:{e.target_id}" for e in refer_to_edges if e.method_id == m.method_id]
    sql_keys_for_m = {e.target_id for e in refer_to_edges if e.method_id == m.method_id and e.target_type == "SqlStatement"}
    rw_summary = ", ".join(f"{rw.table_name}({rw.relation})" for rw in reads_writes if rw.sql_key in sql_keys_for_m)[:200]
    linked_architecture_context = _build_linked_architecture_context(
        m.method_id, refer_to_edges, reads_writes,
        sql_index=sql_index,
        config_index=config_index,
        class_index=class_index,
        total_max_len=LINKED_ARCHITECTURE_CONTEXT_MAX,
    )
    class_comment, class_annotations = _get_class_context(class_index or {}, m.class_key)
    calls: List[str] = []
    dependencies: List[Dict[str, Any]] = []
    stereotype = fallback_stereotype
    if api_key:
        analysis = _invoke_analysis_llm(
            m, api_key, locale,
            calls_outgoing=calls_outgoing,
            reads_writes_summary=rw_summary or None,
            class_comment=class_comment,
            class_annotations=class_annotations,
            linked_architecture_context=linked_architecture_context or None,
        )
        if analysis:
            if analysis.get("summary"):
                summary = analysis["summary"]
            calls = list(analysis.get("calls") or [])
            dependencies = list(analysis.get("dependencies") or [])
            raw_stereotype = (analysis.get("stereotype") or "").strip().lower()
            if raw_stereotype in ("command", "readmodel"):
                stereotype = raw_stereotype
    data = {
        "stereotype": stereotype,
        "summary": summary,
        "fqn": m.fqn,
        "calls": calls,
        "dependencies": dependencies,
    }
    return m.method_id, data


def run_step6(
    method_facts_list: List[MethodFacts],
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    use_llm: bool = False,
    api_key: Optional[str] = None,
    locale: str = "ko",
    parallel: bool = False,
    class_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """메서드별 stereotype, summary. use_llm=True이고 api_key 있으면 LLM으로 summary + calls + dependencies + stereotype 수신 (analysis.yaml).
    parallel=True이면 메서드 단위 병렬. class_index/sql_index/config_index로 연결된 아키텍처 노드(SQL, Config, Class) 컨텍스트 전달."""
    api_key = api_key or os.getenv("LLM_API_KEY", "")
    logger.info(
        "[EXP:STEP6] 요약 진입 | method_facts=%d use_llm=%s api_key_set=%s parallel=%s",
        len(method_facts_list),
        use_llm,
        bool(api_key),
        parallel,
    )
    result: Dict[str, Dict[str, Any]] = {}
    if parallel and use_llm and api_key:
        from exp.config import get_llm_parallel_workers
        workers = get_llm_parallel_workers()
        total = len(method_facts_list)
        workers_actual = min(workers, total)
        logger.info(
            "[EXP:STEP6] 요약 LLM 병렬 시작 | 메서드=%d 워커=%d (메서드당 1요청, 동시에 %d개까지 처리)",
            total,
            workers,
            workers_actual,
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=workers_actual) as executor:
            futures = {
                executor.submit(
                    _process_one_method_for_analysis,
                    m,
                    refer_to_edges,
                    reads_writes,
                    api_key,
                    locale,
                    class_index,
                    sql_index,
                    config_index,
                ): m
                for m in method_facts_list
            }
            for future in as_completed(futures):
                try:
                    method_id, data = future.result()
                    result[method_id] = data
                except Exception as e:
                    m = futures[future]
                    logger.warning("[EXP:STEP6] 병렬 분석 예외 method_id=%s: %s", m.method_id, e)
                    stereotype = compute_stereotype(m.method_id, refer_to_edges, reads_writes)
                    result[m.method_id] = {
                        "stereotype": stereotype,
                        "summary": f"{m.fqn} ({stereotype}); lines {m.start_line}-{m.end_line}",
                        "fqn": m.fqn,
                        "calls": [],
                        "dependencies": [],
                    }
                completed += 1
                if completed == total:
                    logger.info("[EXP:STEP6] analysis LLM 완료  %d/%d", completed, total)
                else:
                    logger.debug("[EXP:STEP6] analysis LLM 진행  %d/%d", completed, total)
    else:
        for m in method_facts_list:
            fallback_stereotype = compute_stereotype(m.method_id, refer_to_edges, reads_writes)
            summary = f"{m.fqn} ({fallback_stereotype}); lines {m.start_line}-{m.end_line}"
            calls_outgoing = [f"{e.target_type}:{e.target_id}" for e in refer_to_edges if e.method_id == m.method_id]
            sql_keys_for_m = {e.target_id for e in refer_to_edges if e.method_id == m.method_id and e.target_type == "SqlStatement"}
            rw_summary = ", ".join(f"{rw.table_name}({rw.relation})" for rw in reads_writes if rw.sql_key in sql_keys_for_m)[:200]
            linked_architecture_context = _build_linked_architecture_context(
                m.method_id, refer_to_edges, reads_writes,
                sql_index=sql_index,
                config_index=config_index,
                class_index=class_index,
                total_max_len=LINKED_ARCHITECTURE_CONTEXT_MAX,
            )
            class_comment, class_annotations = _get_class_context(class_index or {}, m.class_key)
            calls: List[str] = []
            dependencies: List[Dict[str, Any]] = []
            stereotype = fallback_stereotype
            if use_llm and api_key:
                analysis = _invoke_analysis_llm(
                    m, api_key, locale,
                    calls_outgoing=calls_outgoing,
                    reads_writes_summary=rw_summary or None,
                    class_comment=class_comment,
                    class_annotations=class_annotations,
                    linked_architecture_context=linked_architecture_context or None,
                )
                if analysis:
                    if analysis.get("summary"):
                        summary = analysis["summary"]
                    calls = list(analysis.get("calls") or [])
                    dependencies = list(analysis.get("dependencies") or [])
                    raw_stereotype = (analysis.get("stereotype") or "").strip().lower()
                    if raw_stereotype in ("command", "readmodel"):
                        stereotype = raw_stereotype
            result[m.method_id] = {
                "stereotype": stereotype,
                "summary": summary,
                "fqn": m.fqn,
                "calls": calls,
                "dependencies": dependencies,
            }
    logger.info("[EXP:STEP6] 요약 완료 | summary_count=%d", len(result))
    return result


def _build_field_code_map(
    data_dir: str,
    run_dir: Optional[str],
) -> Dict[str, str]:
    """ChunkBundle에서 field_id -> code 맵 구성. run_dir 있으면 해당 chunk_bundles 사용."""
    from exp.config import get_chunk_bundle_dir_from_run_dir
    from exp.chunk_bundle import load_field_chunks_from_bundles
    chunk_dir = get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    chunks = load_field_chunks_from_bundles(data_dir or "", chunk_dir)
    return {c["field_id"]: (c.get("code") or "") for c in chunks if c.get("field_id") and c.get("code")}


def _get_field_code_from_ref(
    code_ref: Optional[str],
    start_line: int,
    end_line: int,
    data_dir: str,
) -> str:
    """code_ref(path:start-end)와 라인 범위로 소스에서 필드 코드 추출."""
    if not code_ref or ":" not in code_ref:
        return ""
    path_part = code_ref[: code_ref.index(":")]
    path = Path(data_dir) / path_part if data_dir else Path(path_part)
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text_with_lines = add_line_numbers(raw)
        return slice_by_line_range(text_with_lines, start_line, end_line)
    except Exception:
        return ""


def _invoke_field_summary_llm(
    field: FieldFacts,
    code: str,
    class_comment: Optional[str],
    class_annotations: Optional[str],
    api_key: str,
    locale: str = "ko",
) -> Optional[str]:
    """필드 1개에 대해 field_summary 규칙 실행 → summary 반환."""
    if not api_key or not (code or "").strip():
        return None
    inputs: Dict[str, Any] = {
        "code": (code or "").strip(),
        "locale": locale,
        "field_name": field.name,
        "field_type": getattr(field, "field_type", None),
        "field_comment": getattr(field, "comment", None),
        "field_annotations": getattr(field, "annotations", None),
    }
    if class_comment:
        inputs["class_comment"] = class_comment[:500] if len(class_comment) > 500 else class_comment
    if class_annotations:
        inputs["class_annotations"] = class_annotations[:300] if len(class_annotations) > 300 else class_annotations
    try:
        loader = RuleLoader(target_lang="framework")
        result = loader.execute("field_summary", inputs, api_key)
        if isinstance(result, dict) and result.get("summary"):
            return result["summary"]
    except Exception as e:
        logger.warning("[EXP:STEP6] field_summary LLM 실패 field_id=%s: %s", field.field_id, e)
    return None


def run_field_summary(
    field_facts_list: List[FieldFacts],
    class_index: Dict[str, Any],
    data_dir: str = "",
    run_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> Dict[str, Dict[str, Any]]:
    """필드별 요약. 호출 관계 없으므로 병렬만 수행. 클래스·필드 주석/어노테이션 컨텍스트 전달."""
    api_key = api_key or os.getenv("LLM_API_KEY", "")
    if not field_facts_list:
        logger.info("[EXP:STEP6] Field 요약 스킵 | field_facts=0")
        return {}
    field_code_map = _build_field_code_map(data_dir, run_dir)
    logger.info(
        "[EXP:STEP6] Field 요약 진입 | fields=%d api_key_set=%s field_code_from_bundles=%d",
        len(field_facts_list),
        bool(api_key),
        len(field_code_map),
    )

    def process_one(f: FieldFacts) -> tuple[str, Dict[str, Any]]:
        code = field_code_map.get(f.field_id) or _get_field_code_from_ref(
            f.code_ref, f.start_line, f.end_line, data_dir
        )
        class_comment, class_annotations = _get_class_context(class_index, f.class_key)
        summary = ""
        if code.strip():
            if api_key:
                summary = _invoke_field_summary_llm(
                    f, code, class_comment, class_annotations, api_key, locale
                ) or ""
            if not summary:
                summary = f"{f.name} ({getattr(f, 'field_type', '') or '?'}); lines {f.start_line}-{f.end_line}"
        else:
            summary = f"{f.name}; lines {f.start_line}-{f.end_line}"
        return f.field_id, {"summary": summary, "name": f.name, "field_type": getattr(f, "field_type", None)}

    result: Dict[str, Dict[str, Any]] = {}
    from exp.config import get_llm_parallel_workers
    workers = get_llm_parallel_workers()
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_one, f): f for f in field_facts_list}
            for future in as_completed(futures):
                try:
                    field_id, data = future.result()
                    result[field_id] = data
                except Exception as e:
                    f = futures[future]
                    logger.warning("[EXP:STEP6] Field 요약 예외 field_id=%s: %s", f.field_id, e)
                    result[f.field_id] = {
                        "summary": f"{f.name}; lines {f.start_line}-{f.end_line}",
                        "name": f.name,
                        "field_type": getattr(f, "field_type", None),
                    }
    else:
        for f in field_facts_list:
            field_id, data = process_one(f)
            result[field_id] = data
    logger.info("[EXP:STEP6] Field 요약 완료 | summary_count=%d", len(result))
    return result


def _class_key_to_fqn(class_index: Dict[str, Any]) -> Dict[str, str]:
    """class_index에서 class_key -> fqn 매핑."""
    out: Dict[str, str] = {}
    for info in class_index.values() if class_index else []:
        if not isinstance(info, dict):
            continue
        key = info.get("class_key")
        fqn = info.get("fqn")
        if key and fqn:
            out[key] = fqn
    return out


def _invoke_class_summary_llm(
    class_fqn: str,
    items: List[Dict[str, Any]],
    class_comment: Optional[str],
    class_annotations: Optional[str],
    prev_summary: Optional[str],
    api_key: str,
    locale: str = "ko",
) -> Optional[str]:
    """class_summary_batch 규칙 실행 → 클래스 요약 1개 반환."""
    if not api_key or not items:
        return None
    inputs: Dict[str, Any] = {
        "class_fqn": class_fqn,
        "items": items,
        "locale": locale,
    }
    if class_comment:
        inputs["class_comment"] = class_comment[:500] if len(class_comment) > 500 else class_comment
    if class_annotations:
        inputs["class_annotations"] = class_annotations[:300] if len(class_annotations) > 300 else class_annotations
    if prev_summary:
        inputs["prev_summary"] = prev_summary[:800] if len(prev_summary) > 800 else prev_summary
    try:
        loader = RuleLoader(target_lang="framework")
        result = loader.execute("class_summary_batch", inputs, api_key)
        if isinstance(result, dict) and result.get("summary"):
            return result["summary"]
    except Exception as e:
        logger.warning("[EXP:STEP6] class_summary_batch LLM 실패 class_fqn=%s: %s", class_fqn, e)
    return None


def run_class_summary(
    class_index: Dict[str, Any],
    summary_by_method: Dict[str, Dict[str, Any]],
    summary_by_field: Dict[str, Dict[str, Any]],
    method_facts_list: List[MethodFacts],
    field_facts_list: List[FieldFacts],
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> Dict[str, Dict[str, Any]]:
    """클래스별 요약. 메서드/필드가 많으면 청킹 후 prev_summary로 순차 요약."""
    from exp.config import estimate_tokens, MAX_LLM_BATCH_TOKEN
    api_key = api_key or os.getenv("LLM_API_KEY", "")
    key_to_fqn = _class_key_to_fqn(class_index)
    # class_key -> [(name, summary), ...]
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for m in method_facts_list or []:
        ck = getattr(m, "class_key", None)
        if not ck:
            continue
        name = (m.fqn or "").rsplit(".", 1)[-1] if m.fqn else m.method_id or ""
        data = summary_by_method.get(m.method_id) if summary_by_method else {}
        summary = (data or {}).get("summary", "") or f"{name}; method"
        by_class.setdefault(ck, []).append({"name": name, "summary": summary})
    for f in field_facts_list or []:
        ck = getattr(f, "class_key", None)
        if not ck:
            continue
        data = summary_by_field.get(f.field_id) if summary_by_field else {}
        summary = (data or {}).get("summary", "") or f"{f.name}; field"
        by_class.setdefault(ck, []).append({"name": f.name, "summary": summary})
    result: Dict[str, Dict[str, Any]] = {}
    for class_key, items in by_class.items():
        class_fqn = key_to_fqn.get(class_key) or class_key
        class_comment, class_annotations = _get_class_context(class_index, class_key)
        if not items:
            result[class_fqn] = {"summary": ""}
            continue
        # Chunk items by token count
        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_tokens = 0
        for item in items:
            t = estimate_tokens((item.get("name") or "") + " " + (item.get("summary") or ""))
            if current and current_tokens + t > MAX_LLM_BATCH_TOKEN:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(item)
            current_tokens += t
        if current:
            chunks.append(current)
        prev_summary: Optional[str] = None
        for chunk in chunks:
            if api_key:
                prev_summary = _invoke_class_summary_llm(
                    class_fqn, chunk, class_comment, class_annotations, prev_summary, api_key, locale
                ) or prev_summary
            else:
                prev_summary = "; ".join(f"{x.get('name', '')}: {x.get('summary', '')}" for x in chunk)[:500]
        result[class_fqn] = {"summary": prev_summary or ""}
    logger.info("[EXP:STEP6] Class 요약 완료 | class_count=%d", len(result))
    return result


def run_user_story_generation(
    summary_by_class: Dict[str, Dict[str, Any]],
    class_index: Dict[str, Any],
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> List[Dict[str, Any]]:
    """클래스 요약 기반 User Story 생성 (framework class_user_story 규칙 활용). 반환: user_stories 목록. API 키 없거나 규칙 미사용 시 빈 목록."""
    if not api_key or not summary_by_class:
        logger.info("[EXP:STEP6] User Story 스킵 | api_key=%s summary_by_class 개수=%d", bool(api_key), len(summary_by_class or {}))
        return []
    with_summary = [(fqn, d) for fqn, d in (summary_by_class or {}).items() if (d or {}).get("summary")]
    logger.info("[EXP:STEP6] User Story 생성 시작 | 클래스 총 %d개, summary 있음 %d개", len(summary_by_class), len(with_summary))
    user_stories: List[Dict[str, Any]] = []
    try:
        loader = RuleLoader(target_lang="framework")
        for class_fqn, data in summary_by_class.items():
            summary = (data or {}).get("summary", "")
            if not summary:
                continue
            try:
                result = loader.execute("class_user_story", {"summary": summary, "locale": locale}, api_key)
                if not isinstance(result, dict):
                    logger.warning("[EXP:STEP6] class_user_story 비정상 응답(딕셔너리 아님) class_fqn=%s type=%s", class_fqn, type(result).__name__)
                    continue
                raw = result.get("user_stories")
                if raw and isinstance(raw, list):
                    for us in raw:
                        if isinstance(us, dict):
                            user_stories.append({"class_fqn": class_fqn, **us})
                    logger.info("[EXP:STEP6] class_user_story 완료 class_fqn=%s user_stories=%d", class_fqn, len(raw))
                else:
                    logger.debug("[EXP:STEP6] class_user_story 빈/미포함 class_fqn=%s keys=%s", class_fqn, list(result.keys()) if result else None)
            except Exception as e:
                logger.warning("[EXP:STEP6] class_user_story 실패 class_fqn=%s: %s", class_fqn, e)
    except Exception as e:
        logger.warning("[EXP:STEP6] User Story 생성 스킵: %s", e)
    if user_stories:
        logger.info("[EXP:STEP6] User Story 생성 완료 | 총 %d건", len(user_stories))
    return user_stories


def _invoke_method_summary_enhancement(
    current_summary: str,
    called_summaries: List[Dict[str, Any]],
    api_key: str,
    locale: str = "ko",
    class_name: Optional[str] = None,
    class_comment: Optional[str] = None,
    class_annotations: Optional[str] = None,
) -> Optional[str]:
    """method_summary_enhancement 규칙 실행 → 보강된 summary 반환."""
    if not api_key or not current_summary:
        return current_summary
    inputs: Dict[str, Any] = {
        "current_summary": current_summary,
        "called_methods": called_summaries,
        "locale": locale,
    }
    if class_name:
        inputs["class_name"] = class_name
    if class_comment:
        inputs["class_comment"] = class_comment
    if class_annotations:
        inputs["class_annotations"] = class_annotations
    try:
        loader = RuleLoader(target_lang="framework")
        result = loader.execute("method_summary_enhancement", inputs, api_key)
        if isinstance(result, dict) and result.get("summary"):
            return result["summary"]
    except Exception as e:
        logger.warning("[EXP:STEP6] method_summary_enhancement 실패: %s", e)
    return current_summary


def run_summary_enhancement_by_call_graph(
    summary_by_method: Dict[str, Dict[str, Any]],
    calls_edges: List[Any],
    method_facts_list: List[MethodFacts],
    api_key: Optional[str] = None,
    locale: str = "ko",
    class_index: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """호출 그래프 기준 리프→상위 순으로 동일 레벨 병렬 보강. framework Phase 2.0과 동일 개념."""
    api_key = api_key or os.getenv("LLM_API_KEY", "")
    method_by_id: Dict[str, MethodFacts] = {m.method_id: m for m in method_facts_list}
    node_ids = set(summary_by_method.keys())
    children: Dict[str, List[str]] = {}
    parents: Dict[str, List[str]] = {}
    for e in calls_edges or []:
        c = getattr(e, "caller_method_id", None) if not isinstance(e, dict) else e.get("caller_method_id")
        cal = getattr(e, "callee_method_id", None) if not isinstance(e, dict) else e.get("callee_method_id")
        if not c or not cal or c not in node_ids or cal not in node_ids:
            continue
        children.setdefault(c, []).append(cal)
        parents.setdefault(cal, []).append(c)
    leaf_ids = [n for n in node_ids if n not in children]
    remaining_children: Dict[str, int] = {c: len(children[c]) for c in children}
    summary_cache: Dict[str, str] = {}
    for mid in leaf_ids:
        summary_cache[mid] = (summary_by_method.get(mid) or {}).get("summary", "")
    ready_queue: List[str] = []
    for mid in leaf_ids:
        for p in parents.get(mid, []):
            if p in remaining_children:
                remaining_children[p] -= 1
                if remaining_children[p] == 0:
                    ready_queue.append(p)
    if not ready_queue:
        logger.info("[EXP:STEP6] 호출 그래프 보강: 보강 대상 노드 없음 (리프만 있음)")
        return summary_by_method
    from exp.config import get_llm_parallel_workers
    workers = get_llm_parallel_workers()
    step = 0
    while ready_queue:
        step += 1
        current_batch = list(ready_queue)
        ready_queue = []
        logger.info("[EXP:STEP6] 호출 그래프 보강 Step %d | 레벨 노드 %d개 (병렬)", step, len(current_batch))

        def enhance_one(node_id: str) -> tuple[str, str]:
            data = summary_by_method.get(node_id) or {}
            current_summary = summary_cache.get(node_id) or data.get("summary", "")
            if not current_summary:
                return node_id, current_summary
            called_summaries = []
            for callee_id in children.get(node_id, []):
                if callee_id not in summary_cache:
                    continue
                callee_m = method_by_id.get(callee_id)
                if callee_m:
                    fqn = getattr(callee_m, "fqn", "") or ""
                    class_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else ""
                    class_name = class_fqn.split(".")[-1] if class_fqn else ""
                    method_name = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
                    called_summaries.append({
                        "class_name": class_name,
                        "method_name": method_name,
                        "summary": summary_cache[callee_id],
                    })
            caller_m = method_by_id.get(node_id)
            class_name = None
            class_comment = None
            class_annotations = None
            if caller_m:
                fqn = getattr(caller_m, "fqn", "") or ""
                class_name = fqn.rsplit(".", 1)[0].split(".")[-1] if "." in fqn else None
                class_comment, class_annotations = _get_class_context(class_index or {}, getattr(caller_m, "class_key", None))
            enhanced = _invoke_method_summary_enhancement(
                current_summary,
                called_summaries,
                api_key,
                locale,
                class_name=class_name,
                class_comment=class_comment,
                class_annotations=class_annotations,
            )
            return node_id, (enhanced or current_summary)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(enhance_one, nid): nid for nid in current_batch}
            for future in as_completed(futures):
                nid = futures[future]
                try:
                    node_id, enhanced_summary = future.result()
                    summary_cache[node_id] = enhanced_summary
                    processed_id = node_id
                except Exception as e:
                    logger.warning("[EXP:STEP6] 보강 예외 method_id=%s: %s", nid, e)
                    summary_cache[nid] = (summary_by_method.get(nid) or {}).get("summary", "")
                    processed_id = nid
                if processed_id in parents:
                    for p in parents[processed_id]:
                        if p in remaining_children:
                            remaining_children[p] -= 1
                            if remaining_children[p] == 0:
                                ready_queue.append(p)
    out = dict(summary_by_method)
    for mid, summary in summary_cache.items():
        if mid in out:
            out[mid] = {**out[mid], "summary": summary}
    logger.info("[EXP:STEP6] 호출 그래프 보강 완료 | summary_count=%d", len(out))
    return out
