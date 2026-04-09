# LLM analysis.calls / dependencies → CallsEdge, UsesEdge 해상도. module_index·function_facts_list 사용.
# Phase5 CALLS 3단: CallBatch(ChunkBundle)→Call LLM Extract→Deterministic Link. Function→Function CALLS 생성.

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.common.models import (
    CallsEdge,
    UsesEdge,
    ChunkBundle,
)
from analyzer.strategy.framework.linking.deterministic import (
    TypeMap3Layer,
    resolve_obj_to_type,
    build_type_map_3layer,
    fact_to_module_map,
    build_module_map_with_scope,
)
from util.text_utils import (
    calculate_code_token as estimate_tokens,
)
logger = logging.getLogger(__name__)




# call_code에서 receiver.functionName( 휴리스틱 추출
OBJ_METHOD_PATTERN = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")
# 객체 없이 functionName( 만 있는 경우 (같은 모듈 내부 호출)
BARE_METHOD_PATTERN = re.compile(r"(?:^|=\s*)(\w+)\s*\(")

def _load_stdlib(lang: str) -> frozenset:
    """lang_profile.yaml 기반 범용 stdlib 로드."""
    from analyzer.strategy.framework.common.lang_profile import get_lang_profile
    try:
        return get_lang_profile().stdlib_classes(lang)
    except Exception as e:
        logger.warning("stdlib 로드 실패 | lang=%s error=%s", lang, e)
        return frozenset()


# 현재 파이프라인 실행 중 사용할 stdlib 및 언어 (lang 파라미터로 동적 설정)
_ACTIVE_STDLIB: frozenset = frozenset()
_ACTIVE_LANG: str = ""


def set_active_stdlib(lang: str) -> None:
    """파이프라인 시작 시 호출하여 해당 언어의 stdlib로 전환."""
    global _ACTIVE_STDLIB, _ACTIVE_LANG
    _ACTIVE_STDLIB = _load_stdlib(lang)
    _ACTIVE_LANG = lang


def load_call_records_from_chunk_bundles(
    data_dir: Optional[str] = None,
    chunk_bundle_dir: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """ChunkBundle에서 function_id별 call_records [{call_id, call_code}] 로드.
    chunk_bundle_dir 있으면 해당 디렉터리의 chunk_bundles 사용, 없으면 data_dir/chunk_bundles."""
    from analyzer.strategy.framework.parsing.chunk_bundle import (
        _get_chunk_bundle_dir,
        load_chunk_bundle,
    )
    base = Path(chunk_bundle_dir) if chunk_bundle_dir else Path(_get_chunk_bundle_dir(data_dir or ""))
    if not base.is_dir():
        logger.info("[PHASE:2:CALL_LLM] ChunkBundle 로드 스킵 | reason=chunk_bundle_dir_not_found path=%s", base)
        return {}
    call_records_by_function: Dict[str, List[Dict[str, Any]]] = {}
    for path in base.glob("*.json"):
        try:
            bundle = load_chunk_bundle(path)
            for c in bundle.function_calls:
                call_records_by_function.setdefault(c.parent_function_id, []).append({
                    "call_id": c.call_id,
                    "call_code": c.call_code,
                })
            for v in bundle.local_vars:
                if not v.flags or not v.flags.get("initializerContainsMethodCall"):
                    continue
                function_id = v.parent_function_id
                recs = call_records_by_function.setdefault(function_id, [])
                seq = len(recs)
                call_id = f"C:{v.start_line}:{v.end_line}:{seq}"
                recs.append({
                    "call_id": call_id,
                    "call_code": (v.initializer_code or "").strip(),
                })
        except Exception as e:
            logger.debug("ChunkBundle 로드 스킵 %s: %s", path, e)
    # stdlib/인프라 호출 필터 + 중복 제거 (LLM 토큰 절감)
    # _ACTIVE_STDLIB (lang_profile.yaml + c_stdlib.json 등) 기반 — 하드코딩 없음
    removed_total = 0
    for fid in list(call_records_by_function.keys()):
        recs = call_records_by_function[fid]
        seen_codes: set = set()
        filtered: list = []
        for r in recs:
            cc = (r.get("call_code") or "").strip()
            # 중복 제거
            if cc in seen_codes:
                removed_total += 1
                continue
            seen_codes.add(cc)
            # stdlib/인프라 호출 제거 (기존 _is_stdlib_module 활용)
            func_name = cc.split("(")[0].strip().split()[-1] if "(" in cc else ""
            if func_name and _is_stdlib_module(func_name):
                removed_total += 1
                continue
            filtered.append(r)
        call_records_by_function[fid] = filtered

    total_records = sum(len(recs) for recs in call_records_by_function.values())
    logger.info(
        "[PHASE:2:CALL_LLM] ChunkBundle 로드 완료 | methods_with_calls=%d total_records=%d (removed=%d infra/dup)",
        len(call_records_by_function),
        total_records,
        removed_total,
    )
    return call_records_by_function


def extract_obj_method_from_call_code(call_code: str) -> Tuple[Optional[str], Optional[str]]:
    """call_code에서 objName, functionName 정적 추출 (정적 분석 모드 전용)."""
    if not call_code:
        return None, None
    m = OBJ_METHOD_PATTERN.search(call_code)
    if m:
        return m.group(1), m.group(2)
    # 객체 프리픽스 없는 bare 호출 → this 호출로 간주
    bare = BARE_METHOD_PATTERN.search(call_code)
    if bare:
        function_name = bare.group(1)
        # 언어 키워드/제어문은 제외
        if function_name not in ("new", "return", "throw", "if", "while", "for", "switch", "try", "catch"):
            return "this", function_name
    return None, None


def _call_item_tokens(function_id: str, calls: List[Dict[str, Any]]) -> int:
    """한 (function_id, calls) 항목의 토큰 수 근사 (배치 묶기용)."""
    t = estimate_tokens(function_id or "")
    for c in calls or []:
        t += estimate_tokens(str(c.get("call_code") or ""))
    return max(1, t)


def _build_call_batches(
    call_records_by_function: Dict[str, List[Dict[str, Any]]],
    max_tokens: int,
) -> List[List[Tuple[str, List[Dict[str, Any]]]]]:
    """function_id별 call 목록을 max_tokens 이하로 묶어 배치 리스트 반환. 각 배치는 [(function_id, calls), ...]."""
    batches: List[List[Tuple[str, List[Dict[str, Any]]]]] = []
    current: List[Tuple[str, List[Dict[str, Any]]]] = []
    current_tokens = 0
    for function_id, recs in call_records_by_function.items():
        calls_input = [{"call_id": r.get("call_id", ""), "call_code": r.get("call_code", "")} for r in recs]
        t = _call_item_tokens(function_id, calls_input)
        if t > max_tokens:
            t = max_tokens
        if current and current_tokens + t > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((function_id, calls_input))
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _extract_calls_batch_via_llm(
    batch: List[Tuple[str, List[Dict[str, Any]]]],
    api_key: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """한 배치(여러 function_id + calls)를 한 번에 LLM에 보내 obj_name/function_name 추출. function_id -> results 반환."""
    if not batch or not api_key:
        return {}
    # 언어명을 LLM 프롬프트 템플릿에 전달 (C, Java 등)
    lang_display = {"c": "C", "java": "Java", "python": "Python", "go": "Go", "kotlin": "Kotlin"}.get(_ACTIVE_LANG, _ACTIVE_LANG)
    items = [{"function_id": mid, "calls": calls} for mid, calls in batch]
    try:
        from util.prompt_registry import prompts
        result = prompts.call_extract(items, lang_display, api_key)
    except Exception as e:
        logger.error("[PHASE:2:CALL_LLM] LLM call_extract 오류 | error=%s", e, exc_info=True)
        return {mid: [] for mid, _ in batch}
    out: Dict[str, List[Dict[str, Any]]] = {}
    results_list = result.get("results") or []
    for i, (function_id, _) in enumerate(batch):
        if i < len(results_list) and isinstance(results_list[i], dict):
            res = results_list[i]
            out[function_id] = list(res.get("results") or [])
        else:
            out[function_id] = []
    for mid, _ in batch:
        if mid not in out:
            out[mid] = []
    logger.info(
        "[PHASE:2:CALL_LLM] LLM call_extract 완료 | 배치 함수 수=%d",
        len(batch),
    )
    return out


def _is_stdlib_module(name: Optional[str]) -> bool:
    """표준/기본 라이브러리 모듈이면 True. LLM 방어 로직용."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip()
    if not n:
        return False
    return n in _ACTIVE_STDLIB or n.split(".")[-1] in _ACTIVE_STDLIB


def _candidates_for_module_and_method(
    function_index_by_module: Dict[str, Dict[str, List[str]]],
    module_name: str,
    function_name: str,
) -> List[str]:
    """FQN 또는 short name으로 후보 조회. function_index_by_module 키가 FQN이므로 short name이면 끝이 일치하는 키 모두 검사."""
    direct = (function_index_by_module.get(module_name) or {}).get(function_name, [])
    if direct:
        return direct
    # short name: 키가 ".ModuleName"으로 끝나는 경우 수집
    out: List[str] = []
    for fqn, by_function in function_index_by_module.items():
        if fqn == module_name or fqn.endswith("." + module_name):
            out.extend(by_function.get(function_name, []))
    return out


def resolve_calls_deterministic(
    call_records_with_obj_method: List[Dict[str, Any]],  # [{call_id, objName, functionName, parent_function_id}]
    type_map: TypeMap3Layer,
    function_index_by_module: Dict[str, Dict[str, List[str]]],
    method_to_module: Dict[str, str],
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """objName→moduleName (type_map), function_index_by_module[moduleName][functionName] → callee. 1개면 CALLS, 아니면 unresolved."""
    edges: List[CallsEdge] = []
    unresolved: List[Dict[str, Any]] = []
    for r in call_records_with_obj_method:
        caller_id = r.get("parent_function_id")
        obj_name = r.get("objName")
        function_name = r.get("functionName")
        if not caller_id or not function_name:
            logger.debug("[PHASE:2:CALL_LLM] resolve 스킵 | reason=missing_caller_or_function call_id=%s", r.get("call_id"))
            continue
        if obj_name and obj_name != "this":
            moduleName = resolve_obj_to_type(caller_id, obj_name, type_map, method_to_module)
        else:
            moduleName = method_to_module.get(caller_id)  # this 호출로 간주
        if not moduleName:
            unresolved.append(dict(r, moduleName=None))  # external 시 obj_name으로 표시
            logger.debug("[PHASE:2:CALL_LLM] resolve 스킵 | reason=obj_not_resolved obj_name=%s caller_id=%s", obj_name, caller_id)
            continue
        candidates = _candidates_for_module_and_method(function_index_by_module, moduleName, function_name)
        # C 언어 등: obj_name이 없거나 "this"이면 같은 파일에서 먼저 찾고,
        # 없으면 프로젝트 전체에서 함수명으로 검색 (C는 모든 함수가 전역)
        if not candidates and (not obj_name or obj_name in ("this", "")):
            for mod_fqn, by_function in function_index_by_module.items():
                found = by_function.get(function_name, [])
                if found:
                    candidates.extend(found)
        if len(candidates) == 1:
            edges.append(CallsEdge(caller_function_id=caller_id, callee_function_id=candidates[0], call_code=r.get("call_code", ""), scope="internal"))
        else:
            r_with_module = dict(r)
            r_with_module["moduleName"] = moduleName  # external 표시용
            unresolved.append(r_with_module)
            logger.debug(
                "[PHASE:2:CALL_LLM] resolve 스킵 | reason=ambiguous_or_none candidates=%d moduleName=%s function_name=%s",
                len(candidates),
                moduleName,
                function_name,
            )
    logger.info("[PHASE:2:CALL_LLM] deterministic 완료 | edges=%d unresolved=%d", len(edges), len(unresolved))
    return edges, unresolved


def build_external_calls_edges(
    unresolved: List[Dict[str, Any]],
    module_map: Dict[Tuple[str, str], Tuple[str, str]],
) -> List[CallsEdge]:
    """모듈맵에 있고 scope=external인 경우만 external CALLS 엣지 생성.
    모듈맵에 존재하지 않는 (객체명, 타입) → 라이브러리/표준 메서드로 간주하여 CALLS 생성하지 않음."""
    external: List[CallsEdge] = []
    for r in unresolved or []:
        caller_id = r.get("parent_function_id")
        obj_name = r.get("objName") or ""
        function_name = r.get("functionName") or "?"
        if not caller_id or not obj_name:
            continue
        key = (caller_id, obj_name)
        if key not in module_map:
            # 모듈맵에 없음 = 라이브러리/표준 → CALLS 생성 안 함
            continue
        module_name, scope = module_map[key]
        if scope != "external":
            continue
        if _is_stdlib_module(module_name):
            continue
        callee_id = f"external:{module_name}#{function_name}"
        external.append(CallsEdge(
            caller_function_id=caller_id,
            callee_function_id=callee_id,
            call_code=r.get("call_code", ""),
            scope="external",
        ))
    if external:
        logger.info("[PHASE:2:CALL_LLM] external CALLS 엣지 생성 | count=%d", len(external))
    return external


def _external_callee_module(callee_function_id: str) -> Optional[str]:
    """external:ModuleName#methodName → ModuleName. 아니면 None."""
    s = (callee_function_id or "").strip()
    if not s.startswith("external:"):
        return None
    rest = s[len("external:"):]
    if "#" in rest:
        return rest.split("#", 1)[0].strip() or None
    return rest.strip() or None


def derive_uses_from_calls(
    calls_edges: List[CallsEdge],
    function_facts_list: List[Any],
) -> List[UsesEdge]:
    """CallsEdge 리스트에서 (caller_module_fqn, callee_module_fqn) 추출 → UsesEdge. internal·external 모두 포함(framework와 동일)."""
    function_id_to_module_fqn: Dict[str, str] = {}
    for m in function_facts_list or []:
        mid = getattr(m, "function_id", None)
        if not mid:
            continue
        fqn = getattr(m, "fqn", None) or mid
        module_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else fqn
        function_id_to_module_fqn[mid] = module_fqn
    seen: set[Tuple[str, str, str]] = set()  # (from, to, scope)
    out: List[UsesEdge] = []
    for e in calls_edges or []:
        caller_cfqn = function_id_to_module_fqn.get(e.caller_function_id) or (
            e.caller_function_id.rsplit(".", 1)[0] if "." in e.caller_function_id else ""
        )
        if not caller_cfqn:
            continue
        is_external = getattr(e, "scope", "internal") == "external" or getattr(e, "callee_function_id", "").startswith("external:")
        if is_external:
            callee_cfqn = _external_callee_module(e.callee_function_id)
            scope = "external"
        else:
            callee_cfqn = function_id_to_module_fqn.get(e.callee_function_id) or (
                e.callee_function_id.rsplit(".", 1)[0] if "." in e.callee_function_id else ""
            )
            scope = "internal"
        if not callee_cfqn or caller_cfqn == callee_cfqn:
            continue
        key = (caller_cfqn, callee_cfqn, scope)
        if key in seen:
            continue
        seen.add(key)
        out.append(UsesEdge(from_fqn=caller_cfqn, to_fqn=callee_cfqn, scope=scope))
    logger.info("[PHASE:2:CALL_LLM] derive_uses_from_calls | calls=%d -> uses=%d", len(calls_edges or []), len(out))
    return out


def _flatten_one_batch(
    batch: List[Tuple[str, List[Dict[str, Any]]]],
    llm_results: Dict[str, List[Dict[str, Any]]],
    call_records_by_function: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """한 배치의 LLM 결과를 flat 레코드로 변환."""
    flat: List[Dict[str, Any]] = []
    for function_id, _calls_input in batch:
        by_call_id = {
            r.get("call_id"): r
            for r in (llm_results.get(function_id) or [])
            if r.get("call_id")
        }
        for r in call_records_by_function.get(function_id, []):
            cid = r.get("call_id", "")
            info = by_call_id.get(cid, {})
            obj_name = info.get("obj_name") or None
            function_name = info.get("function_name") or None
            if obj_name and _is_stdlib_module(obj_name):
                obj_name, function_name = "", ""
            flat.append({
                "parent_function_id": function_id,
                "call_id": cid,
                "call_code": r.get("call_code", ""),
                "objName": obj_name,
                "functionName": function_name,
            })
    return flat


async def run_call_batch_pipeline_async(
    data_dir: str,
    function_facts_list: List[Any],
    variable_facts_list: List[Any],
    symbol_type_index: Dict[str, Dict[str, str]],
    function_index_by_module: Dict[str, Dict[str, List[str]]],
    use_llm: bool = True,
    api_key: Optional[str] = None,
    run_dir: Optional[str] = None,
    on_batch: Optional[Any] = None,
    lang: str = "",
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """Call 관계 추출 (async 버전). LLMRateLimiter로 전역 동시성 제어."""
    import asyncio
    from util.async_executor import get_rate_limiter
    from analyzer.strategy.framework.parsing.chunk_bundle import _get_chunk_bundle_dir_from_run_dir

    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    call_records_by_function = load_call_records_from_chunk_bundles(data_dir=data_dir, chunk_bundle_dir=chunk_dir)
    if not call_records_by_function:
        return [], []

    type_map = build_type_map_3layer(symbol_type_index, variable_facts_list, function_facts_list, lang=lang)
    m2c = fact_to_module_map(function_facts_list)
    internal_module_fqns = set(function_index_by_module.keys())
    is_domain_module = lambda cn: bool(cn and str(cn).strip() and str(cn).strip()[0].isupper() and not _is_stdlib_module(str(cn).strip()))
    module_map = build_module_map_with_scope(type_map, m2c, internal_module_fqns, is_domain_module)

    all_edges: List[CallsEdge] = []
    all_unresolved: List[Dict[str, Any]] = []
    key = api_key or ""

    if use_llm and key:
        max_tokens = settings.batch.max_batch_token * 2
        batches = _build_call_batches(call_records_by_function, max_tokens)
        limiter = get_rate_limiter()

        from util.async_executor import get_executor
        executor = get_executor()

        async def _process_one(batch):
            llm_out = await asyncio.to_thread(_extract_calls_batch_via_llm, batch, key)
            batch_flat = _flatten_one_batch(batch, llm_out, call_records_by_function)
            batch_edges, batch_unresolved = resolve_calls_deterministic(batch_flat, type_map, function_index_by_module, m2c)
            batch_external = build_external_calls_edges(batch_unresolved, module_map)
            return batch_edges + batch_external, batch_unresolved

        def _on_result(result):
            if result is None:
                return
            batch_edges, batch_unresolved = result
            all_edges.extend(batch_edges)
            all_unresolved.extend(batch_unresolved)
            if on_batch and batch_edges:
                on_batch(batch_edges, batch_unresolved)

        await executor.run_batch(
            items=batches,
            processor=_process_one,
            on_result=_on_result,
            label="CALL_LLM",
        )
    else:
        # 정적 분석 모드 (LLM 없음)
        flat = []
        for function_id, recs in call_records_by_function.items():
            for r in recs:
                call_code = r.get("call_code", "")
                obj_name, function_name = extract_obj_method_from_call_code(call_code)
                if obj_name and _is_stdlib_module(obj_name):
                    obj_name, function_name = None, None
                flat.append({"parent_function_id": function_id, "call_id": r.get("call_id", ""), "call_code": call_code, "objName": obj_name, "functionName": function_name})
        edges, unresolved = resolve_calls_deterministic(flat, type_map, function_index_by_module, m2c)
        external_edges = build_external_calls_edges(unresolved, module_map)
        all_edges = edges + external_edges
        all_unresolved = unresolved
        if on_batch and all_edges:
            on_batch(all_edges, all_unresolved)

    logger.info("[PHASE:2:CALL_LLM] async 완료 | edges=%d unresolved=%d", len(all_edges), len(all_unresolved))
    return all_edges, all_unresolved
