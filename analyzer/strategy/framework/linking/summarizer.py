# ---------------------------------------------------------------------------
# Step 6: 역할 판단 + 요약 - 기존 step6_summary 통합
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.models import (
    CallsEdge,
    FunctionFacts,
    VariableFacts,
    ReferToEdge,
    ReadsWritesEdge,
)
from util.text_utils import (
    add_line_numbers,
    calculate_code_token as estimate_tokens,
    slice_by_line_range,
)
from util.prompt_executor import get_rule_loader

logger = logging.getLogger(__name__)


VALID_STEREOTYPES = frozenset({
    "Endpoint", "Query", "Command", "Getter", "Setter",
    "Lifecycle", "Scheduled", "EventHandler", "Init",
    "Transactional", "Validation",
})

VALID_MODULE_STEREOTYPES = frozenset({
    "Controller", "Service", "Repository", "Entity", "DTO",
    "Config", "Component", "Aspect", "Filter", "Listener",
    "Validator", "Mapper", "Util", "ExceptionHandler",
    "Gateway", "Facade", "Factory", "Scheduler", "Client", "Enum",
    "Module", "DataStructure",
})


def compute_stereotype(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
) -> str:
    """쓰기(WRITES/INSERT/UPDATE/DELETE) 존재 → command, 조회만(상태 변화 없음) → readmodel."""
    sql_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "SqlStatement"}
    writes = any(
        rw.sql_key in sql_keys and rw.relation in ("WRITES", "INSERT", "UPDATE", "DELETE")
        for rw in reads_writes
    )
    if writes:
        return "command"
    return "readmodel"


def _build_module_key_index(module_index: Dict[str, Any]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """module_index에서 module_key -> (comment, annotations) 역인덱스 구성."""
    idx: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for info in (module_index or {}).values():
        if isinstance(info, dict) and info.get("module_key"):
            idx[info["module_key"]] = (info.get("comment"), info.get("annotations"))
    return idx


def _get_module_context(
    module_key_index: Dict[str, Tuple[Optional[str], Optional[str]]],
    module_key: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """module_key_index에서 module_key에 해당하는 comment, annotations 반환. O(1) 조회."""
    if not module_key:
        return None, None
    return module_key_index.get(module_key, (None, None))


def _build_linked_sql_context(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    sql_text_max_len: int = 800,
) -> str:
    """메서드가 참조하는 SQL 노드의 내용(SQL 일부 + 테이블·relation)을 하나의 문자열로 구성. LLM context용."""
    sql_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "SqlStatement"}
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


LINKED_ARCHITECTURE_CONTEXT_MAX = int(os.environ.get("EXP_STEP6_CONTEXT_MAX", "8000"))


def _build_linked_architecture_context(
    function_id: str,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    sql_text_max_len: int = 800,
    config_snippet_max_len: int = 500,
    module_info_max_len: int = 300,
    module_fields_max_len: int = 400,
    total_max_len: int = 0,
) -> str:
    """메서드가 참조하는 모든 아키텍처 노드(SQL, Config, Module)의 내용을 하나의 문자열로 구성. LLM context용."""
    parts: List[str] = []
    sql_ctx = _build_linked_sql_context(function_id, refer_to_edges, reads_writes, sql_index, sql_text_max_len)
    if sql_ctx:
        parts.append(sql_ctx)

    config_keys = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "ConfigSection"}
    for ckey in sorted(config_keys):
        chunk = (config_index or {}).get(ckey)
        snippet = ""
        if chunk is not None:
            if isinstance(chunk, dict):
                snippet = (chunk.get("raw_snippet") or "")[:config_snippet_max_len]
            else:
                snippet = (getattr(chunk, "raw_snippet", "") or "")[:config_snippet_max_len]
        parts.append(f"[연결된 Config] config_key: {ckey} | 내용: {snippet}")

    module_ids = {e.target_id for e in refer_to_edges if e.function_id == function_id and e.target_type == "Module"}
    for cid in sorted(module_ids):
        info = (module_index or {}).get(cid)
        if not info and module_index:
            for k, v in module_index.items():
                if not isinstance(v, dict):
                    continue
                if v.get("fqn") == cid or v.get("module_key") == cid or k == cid:
                    info = v
                    break
        desc = ""
        if isinstance(info, dict):
            name = info.get("name") or info.get("module_key") or cid
            comment = (info.get("comment") or "")[:module_info_max_len]
            desc = f"name: {name}" + (f" | comment: {comment}" if comment else "")
            # 참조 클래스의 필드 정보 포함 (LLM 분석 컨텍스트용)
            ref_module_key = info.get("module_key") or cid
            fields_desc = _build_module_fields_context(ref_module_key, variable_facts_list, max_len=module_fields_max_len)
            if fields_desc:
                desc += f" | fields: {fields_desc}"
        else:
            desc = str(cid)
        parts.append(f"[연결된 Module] target_id: {cid} | {desc}")
    out = " ".join(parts) if parts else ""
    if total_max_len > 0 and len(out) > total_max_len:
        out = out[: total_max_len - 20] + " ... [truncated]"
    return out


def _build_module_fields_context(
    module_key: Optional[str],
    variable_facts_list: Optional[List[VariableFacts]],
    max_len: int = 500,
) -> str:
    """해당 클래스의 필드/상수 선언을 간결한 문자열로 구성. 메서드 분석 context용.

    static final 상수를 우선 포함하여 LLM이 상수값을 해석할 수 있도록 한다.
    """
    if not module_key or not variable_facts_list:
        return ""
    fields = [f for f in variable_facts_list if f.module_key == module_key]
    if not fields:
        return ""
    parts: List[str] = []
    # static final 상수를 우선 배치
    constants = [f for f in fields if f.code and ("static" in f.code.lower() and "final" in f.code.lower())]
    others = [f for f in fields if f not in constants]
    for f in constants + others:
        code_line = (f.code or "").strip()
        if code_line:
            # 한 줄로 압축
            code_line = " ".join(code_line.split())
            parts.append(code_line)
        else:
            entry = f.name
            if f.variable_type:
                entry = f"{f.variable_type} {f.name}"
            parts.append(entry)
    result = " | ".join(parts)
    if len(result) > max_len:
        result = result[:max_len - 15] + " ... [truncated]"
    return result


def _collect_field_initial_values(
    module_key: Optional[str],
    variable_facts_list: Optional[List[VariableFacts]],
) -> str:
    """같은 모듈 내 필드 변수의 초기값 정보를 수집."""
    if not module_key or not variable_facts_list:
        return ""
    fields = [f for f in variable_facts_list if f.module_key == module_key and f.code]
    if not fields:
        return ""
    parts = []
    for f in fields:
        code_line = " ".join((f.code or "").strip().split())
        if code_line and "=" in code_line:
            parts.append(code_line)
    return " | ".join(parts)


def _invoke_analysis_llm(
    m: FunctionFacts,
    api_key: str,
    lang: str,
    locale: str = "ko",
    calls_outgoing: Optional[List[str]] = None,
    reads_writes_summary: Optional[str] = None,
    module_comment: Optional[str] = None,
    module_annotations: Optional[str] = None,
    linked_architecture_context: Optional[str] = None,
    called_method_summaries: Optional[List[Dict[str, Any]]] = None,
    module_fields_context: Optional[str] = None,
    field_initial_values: Optional[str] = None,
    table_sample_data: Optional[str] = None,
    constants_context: Optional[str] = None,
    existing_actors: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """메서드 1개에 대해 analysis 규칙 실행 → summary/dependencies/stereotype.

    called_method_summaries: 하위 메서드들의 summary 리스트.
    호출 그래프 하위 노드부터 summary를 만든 뒤, 상위 노드에서는 이 정보를 context에 포함해
    한 번의 LLM 호출로 최대한 풍부한 summary를 생성하기 위한 용도.
    """
    if not api_key or not (m.code or "").strip():
        reason = "no_api_key" if not api_key else "no_code"
        logger.info("[FW:STEP6] LLM 스킵 | function_id=%s reason=%s", m.function_id, reason)
        return None
    code = m.code.strip()
    ranges = [{"startLine": m.start_line, "endLine": m.end_line}]
    # lang → display_name (Java, C, Python 등)
    try:
        from analyzer.strategy.framework.common.lang_profile import get_lang_profile
        language = get_lang_profile().display_name(lang)
    except Exception:
        language = lang.capitalize()

    from analyzer.strategy.framework.common.ontology import get_ontology
    onto = get_ontology()
    is_oop = onto.is_oop(lang)
    valid_stereotypes = sorted(onto.valid_stereotypes(lang))

    inputs: Dict[str, Any] = {
        "code": code,
        "ranges": ranges,
        "count": 1,
        "locale": locale,
        "language": language,
        "is_oop": is_oop,
        "valid_stereotypes": valid_stereotypes,
    }
    context_parts: List[str] = []
    if module_comment:
        context_parts.append(
            "module_comment: "
            + (module_comment or "")
        )
    if module_annotations:
        context_parts.append(
            "module_annotations: "
            + (module_annotations or "")
        )
    if calls_outgoing:
        context_parts.append("calls_outgoing: " + ", ".join(calls_outgoing[:20]))
    if reads_writes_summary:
        context_parts.append("reads_writes: " + reads_writes_summary)
    if module_fields_context and module_fields_context.strip():
        context_parts.append("[CLASS_CONSTANTS_AND_FIELDS] " + module_fields_context.strip())
    if linked_architecture_context and linked_architecture_context.strip():
        context_parts.append(linked_architecture_context.strip())
    if called_method_summaries:
        # 호출된 메서드들의 summary를 context에 녹여 상위 메서드 summary를 한 번에 생성
        called_snippets: List[str] = []
        for cs in called_method_summaries[:20]:
            try:
                cls = str(cs.get("module_name") or "")
                name = str(cs.get("function_name") or "")
                summ = str(cs.get("summary") or "")
                if not summ:
                    continue
                id_part = f"{cls}.{name}" if cls and name else (name or cls or "")
                called_snippets.append(f"{id_part}: {summ}" if id_part else summ)
            except Exception:
                continue
        if called_snippets:
            context_parts.append("called_method_summaries: " + " || ".join(called_snippets))
    context = "; ".join(context_parts)
    if context.strip():
        inputs["context"] = context
    if field_initial_values and field_initial_values.strip():
        inputs["field_initial_values"] = field_initial_values.strip()
    if table_sample_data and table_sample_data.strip():
        inputs["table_sample_data"] = table_sample_data.strip()
    if constants_context and constants_context.strip():
        inputs["constants_context"] = constants_context.strip()
    if existing_actors and existing_actors.strip():
        inputs["existing_actors"] = existing_actors.strip()
    try:
        loader = get_rule_loader("framework")
        result = loader.execute("function_summary", inputs, api_key=api_key)
        analysis_list = result.get("analysis") if isinstance(result, dict) else None
        if not analysis_list:
            logger.info("[FW:STEP6] LLM 스킵 | function_id=%s reason=empty_analysis_result", m.function_id)
            return None
        out = analysis_list[0]
        logger.info(
            "[FW:STEP6] LLM 분석 완료 | function_id=%s has_summary=%s dependencies=%d",
            m.function_id,
            bool(out.get("summary")),
            len(out.get("dependencies") or []),
        )
        return out
    except Exception as e:
        logger.warning("[FW:STEP6] LLM 분석 실패 function_id=%s error=%s", m.function_id, e)
        return None


def _process_one_method_for_analysis(
    m: FunctionFacts,
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    api_key: str,
    locale: str,
    module_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    module_key_index: Optional[Dict[str, Tuple[Optional[str], Optional[str]]]] = None,
    called_summaries: Optional[List[Dict[str, Any]]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    table_sample_data: Optional[str] = None,
    constants_context: Optional[str] = None,
    referenced_vars_by_function: Optional[Dict[str, List[str]]] = None,
    existing_actors: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """단일 메서드에 대해 stereotype + (summary, dependencies) 계산."""
    cqrs_fallback = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
    summary = f"{m.fqn} ({cqrs_fallback}); lines {m.start_line}-{m.end_line}"
    method_edges = [e for e in refer_to_edges if e.function_id == m.function_id]
    calls_outgoing = [f"{e.target_type}:{e.target_id}" for e in method_edges]
    sql_keys_for_m = {e.target_id for e in method_edges if e.target_type == "SqlStatement"}
    rw_summary = ", ".join(
        f"{rw.table_name}({rw.relation})" for rw in reads_writes if rw.sql_key in sql_keys_for_m
    )[:200]
    linked_architecture_context = _build_linked_architecture_context(
        m.function_id,
        refer_to_edges,
        reads_writes,
        sql_index=sql_index,
        config_index=config_index,
        module_index=module_index,
        variable_facts_list=variable_facts_list,
        total_max_len=LINKED_ARCHITECTURE_CONTEXT_MAX,
    )
    module_comment, module_annotations = _get_module_context(module_key_index or {}, m.module_key)
    fields_ctx = _build_module_fields_context(m.module_key, variable_facts_list)
    dependencies: List[Dict[str, Any]] = []
    table_insights: List[Dict[str, Any]] = []
    scenarios: List[Dict[str, Any]] = []
    stereotype = ""
    actor = ""

    if api_key:
        analysis = _invoke_analysis_llm(
            m,
            api_key,
            locale,
            calls_outgoing=calls_outgoing,
            reads_writes_summary=rw_summary or None,
            module_comment=module_comment,
            module_annotations=module_annotations,
            linked_architecture_context=linked_architecture_context or None,
            called_method_summaries=called_summaries,
            module_fields_context=fields_ctx or None,
            field_initial_values="\n".join(referenced_vars_by_function.get(m.function_id, [])) if referenced_vars_by_function else None,
            table_sample_data=table_sample_data or None,
            constants_context=constants_context or None,
            existing_actors=existing_actors or None,
        )
        if analysis:
            summary = analysis.get("summary") or summary
            actor = analysis.get("actor") or ""
            dependencies = list(analysis.get("dependencies") or [])
            table_insights = list(analysis.get("tableInsights") or [])
            scenarios = list(analysis.get("scenarios") or [])
            raw_stereotype = (analysis.get("stereotype") or "").strip()
            if raw_stereotype in VALID_STEREOTYPES:
                stereotype = raw_stereotype

    # stereotype이 없으면 CQRS fallback에서 유추
    if not stereotype:
        stereotype = "Command" if cqrs_fallback == "command" else "Query"

    data = {
        "stereotype": stereotype,
        "summary": summary,
        "actor": actor,
        "fqn": m.fqn,
        "calls": [],
        "dependencies": dependencies,
        "tableInsights": table_insights,
        "scenarios": scenarios,
    }
    return m.function_id, data


_STEP6_BATCH_SIZE = 20


def run_step6(
    function_facts_list: List[FunctionFacts],
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    lang: str,
    use_llm: bool = False,
    api_key: Optional[str] = None,
    locale: str = "ko",
    parallel: bool = False,
    module_index: Optional[Dict[str, Any]] = None,
    sql_index: Optional[Dict[str, Any]] = None,
    config_index: Optional[Dict[str, Any]] = None,
    calls_edges: Optional[List[CallsEdge]] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    table_sample_data: Optional[str] = None,
    constants_context: Optional[str] = None,
    referenced_vars_by_function: Optional[Dict[str, List[str]]] = None,
    existing_actors: Optional[str] = None,
) -> Generator[Dict[str, Dict[str, Any]], None, None]:
    """메서드별 stereotype, summary — 20개씩 배치로 yield.

    - LLM 사용(O) + calls_edges 있음  → 호출 그래프 하위 → 상위 순으로 레벨별 병렬, 20개씩 yield.
    - 그 외(LLM 미사용 / calls_edges 없음) → 기존 방식대로 메서드 단위 개별 처리, 20개씩 yield.
    """
    api_key = api_key or settings.llm.api_key or os.getenv("LLM_API_KEY", "")
    module_key_index = _build_module_key_index(module_index or {})
    logger.info(
        "[FW:STEP6] 요약 진입 | function_facts=%d use_llm=%s api_key_set=%s parallel=%s calls_edges=%d",
        len(function_facts_list),
        use_llm,
        bool(api_key),
        parallel,
        len(calls_edges or []),
    )
    total_yielded = 0
    effective_api_key = api_key if use_llm else ""

    def _flush_batch(batch: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """배치를 복사하여 반환 (yield 용)."""
        return dict(batch)

    # LLM을 못 쓰는 경우 / 호출 그래프가 없는 경우 → 기존 방식 유지
    if not (use_llm and effective_api_key and calls_edges):
        if parallel and use_llm and effective_api_key:
            workers = settings.concurrency.max_concurrency
            total = len(function_facts_list)
            workers_actual = max(1, min(workers, total))
            logger.info(
                "[FW:STEP6] 요약 LLM 병렬 시작 (그래프 미사용) | 메서드=%d 워커=%d (동시에 %d개까지 처리)",
                total,
                workers,
                workers_actual,
            )
            completed = 0
            batch: Dict[str, Dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers_actual) as executor:
                futures = {
                    executor.submit(
                        _process_one_method_for_analysis,
                        m,
                        refer_to_edges,
                        reads_writes,
                        effective_api_key,
                        locale,
                        module_index,
                        sql_index,
                        config_index,
                        module_key_index,
                        None,
                        variable_facts_list,
                        table_sample_data,
                        constants_context,
                        referenced_vars_by_function,
                        existing_actors,
                    ): m
                    for m in function_facts_list
                }
                for future in as_completed(futures):
                    try:
                        function_id, data = future.result()
                        batch[function_id] = data
                    except Exception as e:
                        m = futures[future]
                        logger.warning("[FW:STEP6] 병렬 분석 예외 function_id=%s: %s", m.function_id, e)
                        stereotype = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
                        batch[m.function_id] = {
                            "stereotype": stereotype,
                            "summary": f"{m.fqn} ({stereotype}); lines {m.start_line}-{m.end_line}",
                            "fqn": m.fqn,
                            "calls": [],
                            "dependencies": [],
                        }
                    completed += 1
                    if len(batch) >= _STEP6_BATCH_SIZE:
                        total_yielded += len(batch)
                        logger.debug("[FW:STEP6] 배치 yield  %d/%d", total_yielded, total)
                        yield _flush_batch(batch)
                        batch.clear()
                    if completed == total:
                        logger.info("[FW:STEP6] analysis LLM 완료  %d/%d", completed, total)
                    else:
                        logger.debug("[FW:STEP6] analysis LLM 진행  %d/%d", completed, total)
            if batch:
                total_yielded += len(batch)
                yield _flush_batch(batch)
        else:
            batch = {}
            for m in function_facts_list:
                try:
                    function_id, data = _process_one_method_for_analysis(
                        m,
                        refer_to_edges,
                        reads_writes,
                        effective_api_key,
                        locale,
                        module_index,
                        sql_index,
                        config_index,
                        module_key_index,
                        None,
                        variable_facts_list,
                        table_sample_data,
                        constants_context,
                        referenced_vars_by_function,
                        existing_actors,
                    )
                    batch[function_id] = data
                except Exception as e:
                    logger.warning("[FW:STEP6] 순차 분석 예외 function_id=%s: %s", m.function_id, e)
                    stereotype = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
                    batch[m.function_id] = {
                        "stereotype": stereotype,
                        "summary": f"{m.fqn} ({stereotype}); lines {m.start_line}-{m.end_line}",
                        "fqn": m.fqn,
                        "calls": [],
                        "dependencies": [],
                    }
                if len(batch) >= _STEP6_BATCH_SIZE:
                    total_yielded += len(batch)
                    yield _flush_batch(batch)
                    batch.clear()
            if batch:
                total_yielded += len(batch)
                yield _flush_batch(batch)
        logger.info("[FW:STEP6] 요약 완료 | summary_count=%d", total_yielded)
        return

    # -------- 호출 그래프 기반 bottom-up 요약 (단일 LLM 호출 경로) --------
    method_by_id: Dict[str, FunctionFacts] = {m.function_id: m for m in function_facts_list}
    node_ids = set(method_by_id.keys())
    children: Dict[str, List[str]] = {}
    parents: Dict[str, List[str]] = {}
    for e in calls_edges or []:
        c = e.caller_function_id
        cal = e.callee_function_id
        if not c or not cal or c not in node_ids or cal not in node_ids:
            continue
        children.setdefault(c, []).append(cal)
        parents.setdefault(cal, []).append(c)

    # 자식이 없는 노드(리프)부터 시작
    leaf_ids = [n for n in node_ids if n not in children]
    remaining_children: Dict[str, int] = {c: len(children[c]) for c in children}

    summary_cache: Dict[str, str] = {}
    result: Dict[str, Dict[str, Any]] = {}

    workers = settings.concurrency.max_concurrency

    def summarize_one(node_id: str) -> Tuple[str, Dict[str, Any]]:
        m = method_by_id[node_id]
        called_list: List[Dict[str, Any]] = []
        for callee_id in children.get(node_id, []):
            if callee_id not in summary_cache:
                continue
            callee_m = method_by_id.get(callee_id)
            if not callee_m:
                continue
            fqn = getattr(callee_m, "fqn", "")
            module_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else ""
            module_name = module_fqn.split(".")[-1] if module_fqn else ""
            function_name = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
            called_list.append(
                {
                    "module_name": module_name,
                    "function_name": function_name,
                    "summary": summary_cache[callee_id],
                }
            )
        _, data = _process_one_method_for_analysis(
            m,
            refer_to_edges,
            reads_writes,
            effective_api_key,
            locale,
            module_index,
            sql_index,
            config_index,
            module_key_index,
            called_list or None,
            variable_facts_list,
            table_sample_data,
            constants_context,
            referenced_vars_by_function,
            existing_actors,
        )
        return node_id, data

    processed: Set[str] = set()
    batch: Dict[str, Dict[str, Any]] = {}
    max_workers = max(1, workers)

    logger.info(
        "[FW:STEP6] 호출 그래프 기반 요약 시작 (즉시 실행 모드) | leaf=%d 전체=%d 워커=%d",
        len(leaf_ids),
        len(node_ids),
        max_workers,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # leaf 노드부터 즉시 submit
        futures = {executor.submit(summarize_one, nid): nid for nid in leaf_ids}

        while futures:
            # 완료된 future 하나씩 처리
            done_iter = as_completed(futures)
            for future in done_iter:
                nid = futures.pop(future)
                try:
                    node_id, data = future.result()
                    result[node_id] = data
                    summary_cache[node_id] = data.get("summary", "")
                    processed.add(node_id)
                    batch[node_id] = data
                    processed_id = node_id
                except Exception as e:
                    logger.warning("[FW:STEP6] 호출 그래프 요약 예외 function_id=%s: %s", nid, e)
                    m = method_by_id[nid]
                    stereotype = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
                    fallback = {
                        "stereotype": stereotype,
                        "summary": f"{m.fqn} ({stereotype}); lines {m.start_line}-{m.end_line}",
                        "fqn": m.fqn,
                        "calls": [],
                        "dependencies": [],
                    }
                    result[nid] = fallback
                    summary_cache[nid] = fallback["summary"]
                    processed.add(nid)
                    batch[nid] = fallback
                    processed_id = nid

                # 부모 노드가 ready되면 즉시 submit
                for p in parents.get(processed_id, []):
                    if p in remaining_children:
                        remaining_children[p] -= 1
                        if remaining_children[p] == 0:
                            futures[executor.submit(summarize_one, p)] = p

                # 20개 모이면 yield
                if len(batch) >= _STEP6_BATCH_SIZE:
                    total_yielded += len(batch)
                    logger.debug("[FW:STEP6] 배치 yield  %d개", len(batch))
                    yield _flush_batch(batch)
                    batch.clear()

                break  # as_completed iterator를 갱신하기 위해 재진입

    # 남은 배치 flush
    if batch:
        total_yielded += len(batch)
        yield _flush_batch(batch)

    # 사이클 등으로 처리 안 된 노드 fallback
    remaining = node_ids - processed
    if remaining:
        logger.warning(
            "[FW:STEP6] 호출 그래프 요약에서 처리되지 않은 노드 존재 | remaining=%d", len(remaining)
        )
        batch = {}
        for mid in remaining:
            m = method_by_id[mid]
            try:
                _, data = _process_one_method_for_analysis(
                    m,
                    refer_to_edges,
                    reads_writes,
                    effective_api_key,
                    locale,
                    module_index,
                    sql_index,
                    config_index,
                    module_key_index,
                    None,
                    variable_facts_list,
                    table_sample_data,
                    constants_context,
                    referenced_vars_by_function,
                    existing_actors,
                )
                batch[mid] = data
            except Exception as e:
                logger.warning("[FW:STEP6] remaining 노드 개별 처리 예외 function_id=%s: %s", mid, e)
                stereotype = compute_stereotype(m.function_id, refer_to_edges, reads_writes)
                batch[mid] = {
                    "stereotype": stereotype,
                    "summary": f"{m.fqn} ({stereotype}); lines {m.start_line}-{m.end_line}",
                    "fqn": m.fqn,
                    "calls": [],
                    "dependencies": [],
                }
            if len(batch) >= _STEP6_BATCH_SIZE:
                total_yielded += len(batch)
                yield _flush_batch(batch)
                batch.clear()
        if batch:
            total_yielded += len(batch)
            yield _flush_batch(batch)

    logger.info("[FW:STEP6] 호출 그래프 기반 요약 완료 | summary_count=%d", total_yielded)


def _build_variable_code_map(
    data_dir: str,
    run_dir: Optional[str],
) -> Dict[str, str]:
    """ChunkBundle에서 variable_id -> code 맵 구성. run_dir 있으면 해당 chunk_bundles 사용."""
    from analyzer.strategy.framework.parsing.chunk_bundle import (
        _get_chunk_bundle_dir_from_run_dir,
        load_field_chunks_from_bundles,
    )
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    chunks = load_field_chunks_from_bundles(data_dir or "", chunk_dir)
    return {c["variable_id"]: (c.get("code") or "") for c in chunks if c.get("variable_id") and c.get("code")}


def _get_variable_code_from_ref(
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


def _invoke_variable_summary_llm(
    field: VariableFacts,
    code: str,
    module_comment: Optional[str],
    module_annotations: Optional[str],
    api_key: str,
    locale: str = "ko",
) -> Optional[str]:
    """필드 1개에 대해 variable_summary 규칙 실행 → summary 반환."""
    if not api_key or not (code or "").strip():
        return None
    inputs: Dict[str, Any] = {
        "code": (code or "").strip(),
        "locale": locale,
        "variable_name": field.name,
        "variable_type": getattr(field, "variable_type", None),
        "variable_comment": getattr(field, "comment", None),
        "variable_annotations": getattr(field, "annotations", None),
    }
    if module_comment:
        inputs["module_comment"] = module_comment
    if module_annotations:
        inputs["module_annotations"] = module_annotations
    try:
        loader = get_rule_loader("framework")
        result = loader.execute("variable_summary", inputs, api_key=api_key)
        if isinstance(result, Dict) and result.get("summary"):
            return result["summary"]
    except Exception as e:
        logger.warning("[FW:STEP6] variable_summary LLM 실패 variable_id=%s: %s", field.variable_id, e)
    return None


def run_variable_summary(
    variable_facts_list: List[VariableFacts],
    module_index: Dict[str, Any],
    data_dir: str = "",
    run_dir: Optional[str] = None,
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> Generator[Dict[str, Dict[str, Any]], None, None]:
    """필드별 요약 — 완료 즉시 yield."""
    api_key = api_key or settings.llm.api_key or os.getenv("LLM_API_KEY", "")
    if not variable_facts_list:
        logger.info("[FW:STEP6] Variable 요약 스킵 | variable_facts=0")
        return
    module_key_index = _build_module_key_index(module_index or {})
    variable_code_map = _build_variable_code_map(data_dir, run_dir)
    logger.info(
        "[FW:STEP6] Variable 요약 진입 | variables=%d api_key_set=%s variable_code_from_bundles=%d",
        len(variable_facts_list),
        bool(api_key),
        len(variable_code_map),
    )

    def process_one(f: VariableFacts) -> Tuple[str, Dict[str, Any]]:
        code = variable_code_map.get(f.variable_id) or _get_variable_code_from_ref(
            f.code_ref,
            f.start_line,
            f.end_line,
            data_dir,
        )
        module_comment, module_annotations = _get_module_context(module_key_index, f.module_key)
        summary = ""
        if code.strip() and api_key:
            summary = _invoke_variable_summary_llm(f, code, module_comment, module_annotations, api_key, locale) or ""
        if not summary:
            if code.strip():
                summary = f"{f.name} ({getattr(f, 'variable_type', '') or '?'}); lines {f.start_line}-{f.end_line}"
            else:
                summary = f"{f.name}; lines {f.start_line}-{f.end_line}"
        return f.variable_id, {
            "summary": summary,
            "name": f.name,
            "variable_type": getattr(f, "variable_type", None),
        }

    _FIELD_BATCH_SIZE = 20
    total_yielded = 0
    batch: Dict[str, Dict[str, Any]] = {}
    workers = settings.concurrency.max_concurrency
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_one, f): f for f in variable_facts_list}
            for future in as_completed(futures):
                try:
                    variable_id, data = future.result()
                    total_yielded += 1
                    batch[variable_id] = data
                except Exception as e:
                    f = futures[future]
                    logger.warning("[FW:STEP6] Variable 요약 예외 variable_id=%s: %s", f.variable_id, e)
                    total_yielded += 1
                    batch[f.variable_id] = {
                        "summary": f"{f.name}; lines {f.start_line}-{f.end_line}",
                        "name": f.name,
                        "variable_type": getattr(f, "variable_type", None),
                    }
                if len(batch) >= _FIELD_BATCH_SIZE:
                    yield batch
                    batch = {}
    else:
        for f in variable_facts_list:
            variable_id, data = process_one(f)
            total_yielded += 1
            batch[variable_id] = data
            if len(batch) >= _FIELD_BATCH_SIZE:
                yield batch
                batch = {}
    if batch:
        yield batch
    logger.info("[FW:STEP6] Variable 요약 완료 | summary_count=%d", total_yielded)




def _invoke_module_summary_llm(
    module_fqn: str,
    items: List[Dict[str, Any]],
    module_comment: Optional[str],
    module_annotations: Optional[str],
    prev_summary: Optional[str],
    api_key: str,
    locale: str = "ko",
    kind: Optional[str] = None,
) -> Tuple[Optional[str], str, Dict[str, List[str]]]:
    """module_summary_batch 규칙 실행 → (모듈 요약, moduleStereotype, unified_actors) 반환."""
    if not api_key or not items:
        return None, "", {}
    inputs: Dict[str, Any] = {
        "module_fqn": module_fqn,
        "items": items,
        "locale": locale,
    }
    if kind:
        inputs["kind"] = kind
    if module_comment:
        inputs["module_comment"] = module_comment
    if module_annotations:
        inputs["module_annotations"] = module_annotations
    if prev_summary:
        inputs["prev_summary"] = prev_summary
    try:
        loader = get_rule_loader("framework")
        result = loader.execute("module_summary", inputs, api_key=api_key)
        if isinstance(result, Dict):
            summary = result.get("summary") or None
            raw_module_st = (result.get("moduleStereotype") or "").strip()
            module_stereotype = raw_module_st if raw_module_st in VALID_MODULE_STEREOTYPES else ""
            unified_actors = result.get("unified_actors") or {}
            return summary, module_stereotype, unified_actors
    except Exception as e:
        logger.warning("[FW:STEP6] module_summary_batch LLM 실패 module_fqn=%s: %s", module_fqn, e)
    return None, "", {}


def run_module_summary(
    module_index: Dict[str, Any],
    summary_by_function: Dict[str, Dict[str, Any]],
    summary_by_variable: Dict[str, Dict[str, Any]],
    function_facts_list: List[FunctionFacts],
    variable_facts_list: List[VariableFacts],
    api_key: Optional[str] = None,
    locale: str = "ko",
) -> Generator[Dict[str, Dict[str, Any]], None, None]:
    """모듈별 요약 — 모듈 완료 즉시 yield."""
    api_key = api_key or settings.llm.api_key or os.getenv("LLM_API_KEY", "")
    module_key_index = _build_module_key_index(module_index or {})
    # module_key → kind 매핑 (FILE, STRUCT, CLASS 등)
    key_to_kind: Dict[str, str] = {}
    for info in (module_index or {}).values():
        if isinstance(info, dict) and info.get("module_key"):
            key_to_kind[info["module_key"]] = info.get("kind", "")

    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for m in function_facts_list or []:
        ck = getattr(m, "module_key", None)
        if not ck:
            continue
        name = (m.fqn or "").rsplit(".", 1)[-1] if m.fqn else m.function_id or ""
        data = summary_by_function.get(m.function_id) or {}
        summary = data.get("summary", "") if isinstance(data, dict) else ""
        if not summary:
            summary = f"{name}; method"
        actor = data.get("actor", "") if isinstance(data, dict) else ""
        item = {"name": name, "summary": summary}
        if actor:
            item["actor"] = actor
        by_class.setdefault(ck, []).append(item)
    for f in variable_facts_list or []:
        ck = getattr(f, "module_key", None)
        if not ck:
            continue
        data = summary_by_variable.get(f.variable_id) or {}
        summary = data.get("summary", "") if isinstance(data, dict) else ""
        if not summary:
            summary = f"{f.name}; field"
        by_class.setdefault(ck, []).append({"name": f.name, "summary": summary})

    def _summarize_one_module(module_key: str, items: List[Dict[str, Any]]) -> Tuple[str, str, str, Dict[str, List[str]]]:
        """단일 모듈 요약 — 스레드 안전. (module_fqn, summary, moduleStereotype, unified_actors) 반환."""
        module_fqn = module_key
        if not items:
            return module_fqn, "", "", {}
        module_comment, module_annotations = _get_module_context(module_key_index, module_key)
        module_kind = key_to_kind.get(module_key)
        chunks: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_tokens = 0
        max_tokens = settings.batch.max_batch_token
        for item in items:
            t = estimate_tokens((item.get("name") or "") + " " + (item.get("summary") or ""))
            if current and current_tokens + t > max_tokens:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(item)
            current_tokens += t
        if current:
            chunks.append(current)
        prev_summary: Optional[str] = None
        module_stereotype = ""
        all_unified_actors: Dict[str, List[str]] = {}
        for chunk in chunks:
            if api_key:
                result_summary, result_stereotype, chunk_unified = _invoke_module_summary_llm(
                    module_fqn,
                    chunk,
                    module_comment,
                    module_annotations,
                    prev_summary,
                    api_key,
                    locale,
                    kind=module_kind,
                )
                prev_summary = result_summary or prev_summary
                if result_stereotype:
                    module_stereotype = result_stereotype
                if chunk_unified:
                    for rep, syns in chunk_unified.items():
                        all_unified_actors.setdefault(rep, []).extend(syns)
            else:
                prev_summary = "; ".join(
                    f"{x.get('name', '')}: {x.get('summary', '')}" for x in chunk
                )
        return module_fqn, prev_summary or "", module_stereotype, all_unified_actors

    _CLASS_BATCH_SIZE = 5
    total_yielded = 0
    batch: Dict[str, Dict[str, Any]] = {}
    workers = min(settings.concurrency.max_concurrency, 4)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_summarize_one_module, ck, items): ck
                for ck, items in by_class.items()
            }
            for future in as_completed(futures):
                try:
                    module_fqn, summary, module_stereotype, unified = future.result()
                except Exception as e:
                    ck = futures[future]
                    module_fqn = ck
                    summary = ""
                    module_stereotype = ""
                    unified = {}
                    logger.warning("[FW:STEP6] Module 요약 예외 module=%s: %s", module_fqn, e)
                total_yielded += 1
                batch[module_fqn] = {"summary": summary, "moduleStereotype": module_stereotype, "unified_actors": unified}
                if len(batch) >= _CLASS_BATCH_SIZE:
                    yield batch
                    batch = {}
    else:
        for module_key, items in by_class.items():
            module_fqn, summary, module_stereotype, unified = _summarize_one_module(module_key, items)
            total_yielded += 1
            batch[module_fqn] = {"summary": summary, "moduleStereotype": module_stereotype, "unified_actors": unified}
            if len(batch) >= _CLASS_BATCH_SIZE:
                yield batch
                batch = {}
    if batch:
        yield batch
    logger.info("[FW:STEP6] Module 요약 완료 | module_count=%d", total_yielded)
