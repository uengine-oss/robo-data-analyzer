"""함수→함수 호출 관계(CALLS) 추출.

Phase 2 Step 2: CallBatch(ChunkBundle)→Call LLM Extract→Deterministic Link.

본 모듈은 함수 호출 도메인 전체를 담는다 (단일 처리 흐름):
  1. 변수명 → 타입 해석 (4-layer 스코프 맵: local/param/call_result/module/runtime_hint)
  2. CallBatch 빌드 (ChunkBundle 청크 → LLM 입력)
  3. Call LLM Extract (LLM 호출 + 결과 파싱)
  4. 결정론적 링킹 (CallsEdge 생성)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.framework.analysis.chunk_bundle import (
    _get_chunk_bundle_dir_from_run_dir,
    load_chunk_bundle,
)
from analyzer.strategy.framework.common.domain.lang_profile import get_lang_profile
from analyzer.strategy.framework.common.models.node_models import (
    CallsEdge,
    UsesEdge,
)
from config.settings import settings
from util.code_text_utils import parse_params
from util.llm_rule_runner import prompts
from util.llm_call_safety import get_executor

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Section 1: 변수명 → 타입 해석 (4-layer 스코프 맵)
# 우선순위: this > runtime_hint > local > param > call_result > module variable
# 언어별 param 스타일: python/kotlin = "name: type", go = "name type", default = "type name"
# ═══════════════════════════════════════════════════════════


@dataclass
class MethodScopeMap:
    """메서드 스코프: local var → type, param → type, call result → type."""
    local: Dict[str, str] = field(default_factory=dict)
    param: Dict[str, str] = field(default_factory=dict)
    call_result_type: Dict[str, str] = field(default_factory=dict)


@dataclass
class ModuleScopeMap:
    """모듈 스코프: member variable → type (MODULE/VARIABLE 노드 기반)."""
    variable: Dict[str, str] = field(default_factory=dict)


@dataclass
class RuntimeHintMap:
    """NEW_INSTANCE 기반 var → 구현체 타입 (인터페이스 → 구현체 확정)."""
    var_to_impl: Dict[str, str] = field(default_factory=dict)


@dataclass
class TypeMap3Layer:
    """함수 ID 별 3층 맵 + module 스코프 + runtime hint."""
    method_scope: Dict[str, MethodScopeMap] = field(default_factory=dict)
    module_scope: Dict[str, ModuleScopeMap] = field(default_factory=dict)
    runtime_hint: Dict[str, RuntimeHintMap] = field(default_factory=dict)


_LANG_PARAM_STYLE = {
    "python": "name_colon_type",
    "kotlin": "name_colon_type",
    "go": "name_type",
}


def build_type_map_3layer(
    symbol_type_index: Dict[str, Dict[str, str]],
    variable_facts_list: List[Any],
    function_facts_list: List[Any],
    runtime_hint_by_function: Optional[Dict[str, Dict[str, str]]] = None,
    lang: str = "",
) -> TypeMap3Layer:
    """symbol_type_index, VARIABLE, FUNCTION params 로 3층 맵 구성."""
    param_style = _LANG_PARAM_STYLE.get(lang, "type_name")
    type_map = TypeMap3Layer()

    # 1) local var (symbol_type_index)
    for function_id, var_types in symbol_type_index.items():
        type_map.method_scope[function_id] = MethodScopeMap(local=dict(var_types))

    # 2) function params
    for m in function_facts_list:
        function_id = getattr(m, "function_id", None)
        if not function_id:
            continue
        params_str = m.params
        if function_id not in type_map.method_scope:
            type_map.method_scope[function_id] = MethodScopeMap()
        type_map.method_scope[function_id].param.update(parse_params(params_str, param_style))

    # 3) module member variables
    for f in variable_facts_list:
        module_key = f.module_key
        variable_name = f.name
        variable_type = f.variable_type
        if not module_key or not variable_name:
            continue
        if module_key not in type_map.module_scope:
            type_map.module_scope[module_key] = ModuleScopeMap()
        type_map.module_scope[module_key].variable[variable_name] = variable_type

    # 4) runtime hints
    if runtime_hint_by_function:
        for function_id, var_impl in runtime_hint_by_function.items():
            type_map.runtime_hint[function_id] = RuntimeHintMap(var_to_impl=dict(var_impl))

    return type_map


def fact_to_module_map(function_facts_list: List[Any]) -> Dict[str, str]:
    """function_id → module_key 매핑."""
    return {
        m.function_id: m.module_key
        for m in function_facts_list
        if m.function_id
    }


def resolve_obj_to_type(
    function_id: str,
    obj_name: str,
    type_map: TypeMap3Layer,
    method_to_module: Dict[str, str],
) -> Optional[str]:
    """obj_name → moduleName/FQN 결정.

    우선순위: ``this`` > runtime_hint > local > param > call_result > module variable.
    """
    if not obj_name:
        return None
    if obj_name.strip() == "this" and function_id:
        return method_to_module.get(function_id)
    rh = type_map.runtime_hint.get(function_id)
    if rh and obj_name in rh.var_to_impl:
        return rh.var_to_impl[obj_name]
    ms = type_map.method_scope.get(function_id)
    if ms and obj_name in ms.local:
        return ms.local[obj_name]
    if ms and obj_name in ms.param:
        return ms.param[obj_name]
    if ms and obj_name in ms.call_result_type:
        return ms.call_result_type[obj_name]
    module_key = method_to_module.get(function_id)
    if module_key:
        cs = type_map.module_scope.get(module_key)
        if cs and obj_name in cs.variable:
            return cs.variable[obj_name]
    return None


def build_module_map_with_scope(
    type_map: TypeMap3Layer,
    method_to_module: Dict[str, str],
    internal_module_fqns: Any,
    is_domain_module: Any,
) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """(function_id, obj_name) → (module_name, scope). scope = 'internal' | 'external'."""
    out: Dict[Tuple[str, str], Tuple[str, str]] = {}
    if not isinstance(internal_module_fqns, set):
        internal_module_fqns = set(internal_module_fqns or [])

    for function_id, ms in type_map.method_scope.items():
        module_key = method_to_module.get(function_id)
        for obj_name, type_name in list(ms.local.items()) + list(ms.param.items()):
            if not type_name or not is_domain_module(type_name):
                continue
            scope = "internal" if _module_in_set(type_name, internal_module_fqns) else "external"
            out[(function_id, obj_name)] = (type_name, scope)
        if module_key and function_id:
            out[(function_id, "this")] = (
                module_key,
                "internal" if module_key in internal_module_fqns else "external",
            )

    for module_key, cs in type_map.module_scope.items():
        for variable_name, variable_type in cs.variable.items():
            if not variable_type or not is_domain_module(variable_type):
                continue
            scope = "internal" if _module_in_set(variable_type, internal_module_fqns) else "external"
            for function_id, m_module in method_to_module.items():
                if m_module == module_key:
                    out[(function_id, variable_name)] = (variable_type, scope)
                    break

    return out


def _module_in_set(module_name: str, internal_module_fqns: set) -> bool:
    """module_name 이 internal set 에 있는지 (FQN 일치 또는 short-name 일치)."""
    if not module_name:
        return False
    if module_name in internal_module_fqns:
        return True
    short = module_name.rsplit(".", 1)[-1] if "." in module_name else module_name
    return any(fqn == module_name or fqn.endswith("." + short) for fqn in internal_module_fqns)


# ═══════════════════════════════════════════════════════════
# Section 2: CALLS 추출 — LLM + 결정론적 매칭
# ═══════════════════════════════════════════════════════════




# call_code에서 receiver.functionName( 휴리스틱 추출
OBJ_METHOD_PATTERN = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")
# 객체 없이 functionName( 만 있는 경우 (같은 모듈 내부 호출)
BARE_METHOD_PATTERN = re.compile(r"(?:^|=\s*)(\w+)\s*\(")

def _load_stdlib(lang: str) -> frozenset:
    """lang_profile.yaml 기반 범용 stdlib 로드."""
    try:
        return get_lang_profile().stdlib_classes(lang)
    except Exception as e:
        logger.error(
            "[CALL_LINKER:STDLIB] stdlib 로드 실패 | lang=%s | %s: %s",
            lang, type(e).__name__, e, exc_info=True,
        )
        raise RuntimeError(
            f"stdlib 로드 실패 (lang={lang}): {type(e).__name__}: {e}"
        ) from e


# 현재 파이프라인 실행 중 사용할 stdlib·언어·코드베이스 패턴 (호출당 동일하므로 글로벌 캐시).
# _ACTIVE_LANG 과 동일한 패턴 — set_* 함수로 진입 시점 1회 설정 → call_extract LLM 호출에서 참조.
_ACTIVE_STDLIB: frozenset = frozenset()
_ACTIVE_LANG: str = ""
_ACTIVE_CODEBASE_PATTERN: str = ""


def set_active_stdlib(lang: str) -> None:
    """파이프라인 시작 시 호출하여 해당 언어의 stdlib로 전환."""
    global _ACTIVE_STDLIB, _ACTIVE_LANG
    _ACTIVE_STDLIB = _load_stdlib(lang)
    _ACTIVE_LANG = lang


def set_active_codebase_pattern(pattern: str) -> None:
    """Phase 2 진입 시 호출하여 call_extract LLM 호출에 주입할 패턴을 설정.

    매크로/함수 구분, CRUD suffix 매핑 등 LLM 추론 정확도 향상에 필요. 빈 문자열이면 미주입.
    """
    global _ACTIVE_CODEBASE_PATTERN
    _ACTIVE_CODEBASE_PATTERN = pattern or ""


def load_call_records_from_chunk_bundles(
    chunk_bundle_dir: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """ChunkBundle에서 function_id별 call_records [{call_id, call_code}] 로드."""
    if not chunk_bundle_dir:
        raise ValueError("chunk_bundle_dir 필수")
    base = Path(chunk_bundle_dir)
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
                    "call_code": v.initializer_code.strip(),
                })
        except Exception as e:
            logger.error(
                "[CHUNK_BUNDLE:LOAD] ChunkBundle 로드 실패 | path=%s | %s: %s",
                path, type(e).__name__, e, exc_info=True,
            )
            raise RuntimeError(
                f"ChunkBundle 로드 실패: {path} ({type(e).__name__}: {e})"
            ) from e
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


def _build_call_batches(
    call_records_by_function: Dict[str, List[Dict[str, Any]]],
    max_records_per_batch: int,
) -> List[List[Tuple[str, List[Dict[str, Any]]]]]:
    """function_id별 call 목록을 호출 개수 기준으로 묶어 배치 리스트 반환.

    LLM 응답 시간은 출력 항목 수에 비례한다(reasoning 모델일수록 더 강함).
    따라서 호출 토큰량이 아닌 호출 개수로 균등 분할한다.

    한 함수의 호출이 max_records_per_batch를 초과하면 여러 배치로 분할한다
    (한 함수가 배치를 독차지해 응답이 길어지는 것을 방지).
    """
    batches: List[List[Tuple[str, List[Dict[str, Any]]]]] = []
    current: List[Tuple[str, List[Dict[str, Any]]]] = []
    current_records = 0
    for function_id, recs in call_records_by_function.items():
        calls_input = [{"call_id": r.get("call_id", ""), "call_code": r.get("call_code", "")} for r in recs]
        # 한 함수의 호출이 너무 많으면 max 단위로 슬라이스
        for offset in range(0, len(calls_input), max_records_per_batch):
            slice_calls = calls_input[offset : offset + max_records_per_batch]
            n = len(slice_calls)
            if current and current_records + n > max_records_per_batch:
                batches.append(current)
                current = []
                current_records = 0
            current.append((function_id, slice_calls))
            current_records += n
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
        result = prompts.call_extract(
            items, lang_display, api_key,
            codebase_pattern=_ACTIVE_CODEBASE_PATTERN,
        )
    except Exception as e:  # noqa: SILENT_FAIL — 한 배치 LLM 호출 실패가 다른 배치를 막지 않도록 errlog 후 빈 결과 반환 (해당 배치 함수들만 CALLS 누락)
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
    records_in_batch = sum(len(calls) for _, calls in batch)
    logger.info(
        "[PHASE:2:CALL_LLM] LLM call_extract 완료 | 배치 records=%d (함수=%d)",
        records_in_batch, len(batch),
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


_ALL_CAPS_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _is_allcaps_macro(name: Optional[str]) -> bool:
    """전부 대문자 (+숫자/언더스코어) 함수명이면 매크로 관용구로 간주. 길이 2 이상만."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip()
    if len(n) < 2:
        return False
    return bool(_ALL_CAPS_MACRO_RE.match(n))


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
    *,
    known_modules: Optional[Set[str]] = None,
) -> List[CallsEdge]:
    """모듈맵에 있고 scope=external 인 경우 external CALLS 엣지 생성.

    추가 — **dispatcher 패턴** (obj_name 비어있고 function_name 이 진짜 타깃 모듈명):
    function_name 이 분석 대상 코드베이스 (``known_modules``) 에 없는 비-stdlib 식별자면
    ``external:<function_name>`` stub 으로 등록. 이 흐름이 없으면 ``mpfm_dlcall("zapamcom10040", ...)``
    같은 cross-module 호출이 그래프에서 누락됨.
    """
    external: List[CallsEdge] = []
    known = known_modules or set()
    for r in unresolved or []:
        caller_id = r.get("parent_function_id")
        obj_name = r.get("objName") or ""
        function_name = r.get("functionName") or ""
        if not caller_id or not function_name:
            continue
        if obj_name:
            # 일반 외부 객체 호출 — 기존 module_map 기반 흐름
            key = (caller_id, obj_name)
            if key not in module_map:
                continue
            module_name, scope = module_map[key]
            if scope != "external":
                continue
            if _is_stdlib_module(module_name):
                continue
            callee_id = f"external:{module_name}#{function_name}"
        else:
            # dispatcher 패턴 — function_name 자체가 모듈명. 코드베이스 매칭 안 되고 stdlib 아니면 external.
            if function_name in known:
                continue
            if _is_stdlib_module(function_name):
                continue
            callee_id = f"external:{function_name}"
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
        is_external = e.scope == "external" or e.callee_function_id.startswith("external:")
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
    function_index_by_module: Optional[Dict[str, Dict[str, List[str]]]] = None,
) -> List[Dict[str, Any]]:
    """한 배치의 LLM 결과를 flat 레코드로 변환.

    LLM 이 같은 call_id 로 여러 번 응답하는 경우 (매크로 래퍼와 그 내부 호출이 둘 다
    추출되는 경우 등) 모두 보존. 2 번째부터 `_1`, `_2` 접미사로 구분.
    매크로(대문자+언더스코어) 는 방어 필터로 `function_name=""` 처리 → edge 생성 차단.

    call_id 는 LLM 매칭용 상관관계 토큰일 뿐 edge 생성엔 안 쓰이므로 접미사 안전.
    `function_index_by_module` 은 현재 미사용 (과거 선호 로직용, 접미사 방식으로 대체됨).
    """
    _ = function_index_by_module  # 호환: 시그니처 유지

    flat: List[Dict[str, Any]] = []
    for function_id, _calls_input in batch:
        # call_id 당 LLM 응답을 리스트로 수집 (중복 가능)
        candidates_by_cid: Dict[str, List[Dict[str, Any]]] = {}
        for r in (llm_results.get(function_id) or []):
            cid = r.get("call_id")
            if not cid:
                continue
            candidates_by_cid.setdefault(cid, []).append(r)

        for r in call_records_by_function.get(function_id, []):
            cid = r.get("call_id", "")
            call_code = r.get("call_code", "")
            cands = candidates_by_cid.get(cid, [])
            if not cands:
                flat.append({
                    "parent_function_id": function_id,
                    "call_id": cid,
                    "call_code": call_code,
                    "objName": None,
                    "functionName": None,
                })
                continue
            for i, cand in enumerate(cands):
                use_cid = cid if i == 0 else f"{cid}_{i}"
                obj_name = cand.get("obj_name") or None
                function_name = cand.get("function_name") or None
                if obj_name and _is_stdlib_module(obj_name):
                    obj_name, function_name = "", ""
                # 방어: 대문자+언더스코어는 매크로 관용구 → 함수 호출 아님
                if _is_allcaps_macro(function_name):
                    obj_name, function_name = "", ""
                flat.append({
                    "parent_function_id": function_id,
                    "call_id": use_cid,
                    "call_code": call_code,
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
    run_dir: str,
    use_llm: bool = True,
    api_key: Optional[str] = None,
    on_batch: Optional[Any] = None,
    lang: str = "",
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """Call 관계 추출 (async 버전). LLMRateLimiter로 전역 동시성 제어."""

    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir)
    call_records_by_function = load_call_records_from_chunk_bundles(chunk_bundle_dir=chunk_dir)
    if not call_records_by_function:
        return [], []

    type_map = build_type_map_3layer(symbol_type_index, variable_facts_list, function_facts_list, lang=lang)
    m2c = fact_to_module_map(function_facts_list)
    internal_module_fqns = set(function_index_by_module.keys())
    is_domain_module = lambda cn: bool(cn and str(cn).strip() and str(cn).strip()[0].isupper() and not _is_stdlib_module(str(cn).strip()))
    module_map = build_module_map_with_scope(type_map, m2c, internal_module_fqns, is_domain_module)
    # dispatcher 패턴 (obj_name="" + function_name=모듈명) external 판정용 — 코드베이스 모듈 짧은 이름 set
    known_modules: Set[str] = set()
    for fqn in internal_module_fqns:
        known_modules.add(fqn)
        if "." in fqn:
            known_modules.add(fqn.rsplit(".", 1)[-1])

    all_edges: List[CallsEdge] = []
    all_unresolved: List[Dict[str, Any]] = []
    key = api_key or ""

    if use_llm and key:
        batches = _build_call_batches(
            call_records_by_function,
            settings.batch.max_call_records_per_batch,
        )
        executor = get_executor()

        async def _process_one(batch):
            llm_out = await asyncio.to_thread(_extract_calls_batch_via_llm, batch, key)
            batch_flat = _flatten_one_batch(batch, llm_out, call_records_by_function, function_index_by_module)
            batch_edges, batch_unresolved = resolve_calls_deterministic(batch_flat, type_map, function_index_by_module, m2c)
            batch_external = build_external_calls_edges(batch_unresolved, module_map, known_modules=known_modules)
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
        external_edges = build_external_calls_edges(unresolved, module_map, known_modules=known_modules)
        all_edges = edges + external_edges
        all_unresolved = unresolved
        if on_batch and all_edges:
            on_batch(all_edges, all_unresolved)

    logger.info("[PHASE:2:CALL_LLM] async 완료 | edges=%d unresolved=%d", len(all_edges), len(all_unresolved))
    return all_edges, all_unresolved
