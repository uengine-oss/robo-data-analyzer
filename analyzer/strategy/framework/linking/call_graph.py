# LLM analysis.calls / dependencies → CallsEdge, UsesEdge 해상도. class_index·method_facts_list 사용.
# Phase5 CALLS 3단: CallBatch(ChunkBundle)→Call LLM Extract→Deterministic Link. Method→Method CALLS 생성.

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from analyzer.strategy.framework.models import (
    CallsEdge,
    UsesEdge,
    MethodFacts,
    ChunkBundle,
    ChunkBundleFile,
    ChunkBundleField,
    ChunkBundleMethod,
    ChunkBundleMethodCall,
    ChunkBundleVariable,
)
from analyzer.strategy.framework.linking.type_resolver import (
    TypeMap3Layer,
    resolve_obj_to_type,
    build_type_map_3layer,
    method_to_class_map,
    build_class_map_with_scope,
)
from util.text_utils import (
    calculate_code_token as estimate_tokens,
    parse_java_params,
)
from util.prompt_executor import get_rule_loader

logger = logging.getLogger(__name__)


def build_param_type_map_from_params(params: str) -> Dict[str, str]:
    """parameters 문자열에서 paramName → paramType 매핑."""
    return parse_java_params(params)


def build_field_map_for_class(
    field_facts_list: List[Any],
    class_key: str,
) -> Dict[str, str]:
    """해당 클래스 필드명 → 타입 매핑."""
    out: Dict[str, str] = {}
    for f in field_facts_list:
        if getattr(f, "class_key", None) != class_key:
            continue
        name = getattr(f, "name", None)
        ft = getattr(f, "field_type", None)
        if name and ft:
            out[name] = ft
    return out


def _resolve_target_fqn(class_index: Dict[str, Any], target_class: str) -> str | None:
    """target_class(단순명 또는 fqn) → class fqn. class_index에 fqn·단순명 둘 다 키로 있음."""
    info = class_index.get(target_class)
    if info and isinstance(info, dict):
        return info.get("fqn") or info.get("class_key")
    return None


def _find_callee_method_id(
    method_facts_list: List[MethodFacts],
    class_index: Dict[str, Any],
    target_class: str,
    method_name: str,
) -> str | None:
    """대상 클래스 fqn + 메서드명 → callee method_id. 첫 번째 매칭 반환."""
    target_fqn = _resolve_target_fqn(class_index, target_class)
    if not target_fqn or not method_name:
        return None
    for m in method_facts_list:
        if not m.fqn:
            continue
        class_part = m.fqn.rsplit(".", 1)[0]
        name_part = m.fqn.rsplit(".", 1)[-1]
        if class_part == target_fqn and name_part == method_name:
            return m.method_id
    return None


def resolve_calls_and_uses(
    summary_by_method: Dict[str, Dict[str, Any]],
    method_facts_list: List[MethodFacts],
    class_index: Dict[str, Any],
    field_facts_list: List[Any],
) -> Tuple[List[CallsEdge], List[UsesEdge]]:
    """Step6 LLM 결과에서 calls/dependencies 수집 → class_index·method_facts_list로 해상도 후 CallsEdge, UsesEdge 리스트 반환."""
    logger.info("[FW:CALL_RESOLVE] 진입 | summary_methods=%d", len(summary_by_method))
    calls_edges: List[CallsEdge] = []
    uses_edges: List[UsesEdge] = []
    method_by_id: Dict[str, MethodFacts] = {m.method_id: m for m in method_facts_list}

    for method_id, data in summary_by_method.items():
        calls = data.get("calls") or []
        dependencies = data.get("dependencies") or []
        caller = method_by_id.get(method_id)
        if not caller:
            logger.info("[FW:CALL_RESOLVE] 스킵 | reason=caller_not_found method_id=%s", method_id)
            continue
        caller_class_fqn = method_id.rsplit(".", 1)[0] if "." in method_id else ""
        param_type_map = build_param_type_map_from_params(caller.params or "")
        field_map = build_field_map_for_class(field_facts_list, caller.class_key or "")

        # dependencies에서 local_type_map 구축 (LLM이 추출한 로컬 변수/파라미터 타입 정보)
        local_type_map: Dict[str, str] = {}
        for dep in dependencies:
            if not isinstance(dep, dict):
                continue
            usage = (dep.get("usage") or "").strip()
            target_cls = (dep.get("targetClass") or "").strip()
            src_member = (dep.get("sourceMember") or "").strip()
            if not target_cls:
                continue
            # local_new/local_var: sourceMember가 메서드명이므로 직접 매핑 안 됨
            # 대신 targetClass 자체를 소문자 첫글자로 변환해서 추정
            # ex) LoanApplication → loanApplication
            if usage in ("local_new", "local_var") and target_cls[0].isupper():
                inferred_var = target_cls[0].lower() + target_cls[1:]
                local_type_map[inferred_var] = target_cls
            # field_call: sourceMember가 메서드명, targetClass가 필드 타입
            # field_map에서 이미 처리됨

        for call_expr in calls:
            if not isinstance(call_expr, str) or "." not in call_expr:
                continue
            parts = call_expr.split(".", 1)
            object_name = parts[0].strip()
            method_name = (parts[1].strip() if len(parts) > 1 else "") or ""
            if not object_name or not method_name:
                continue
            target_class = (
                local_type_map.get(object_name)
                or param_type_map.get(object_name)
                or field_map.get(object_name)
            )
            if not target_class and object_name and object_name[0].isupper():
                target_class = object_name
            if not target_class:
                logger.debug("[FW:CALL_RESOLVE] 스킵 | reason=obj_type_unknown obj=%s call=%s", object_name, call_expr)
                continue
            callee_method_id = _find_callee_method_id(method_facts_list, class_index, target_class, method_name)
            if callee_method_id:
                calls_edges.append(CallsEdge(caller_method_id=method_id, callee_method_id=callee_method_id, call_code=call_expr))
            else:
                # 프로젝트 내 못 찾음 → external CALLS 생성
                target_fqn_ext = _resolve_target_fqn(class_index, target_class)
                if target_fqn_ext:
                    # class_index에 있는데 메서드가 없음 (인터페이스 등) → internal unresolved, 스킵
                    logger.debug("[FW:CALL_RESOLVE] 스킵 | reason=method_not_found_in_internal class=%s method=%s", target_class, method_name)
                elif not _is_stdlib_class(target_class):
                    # 표준 라이브러리가 아닌 외부 클래스 → external CALLS
                    ext_callee_id = f"external:{target_class}#{method_name}"
                    calls_edges.append(CallsEdge(
                        caller_method_id=method_id, callee_method_id=ext_callee_id,
                        call_code=call_expr, scope="external",
                    ))
            target_fqn = _resolve_target_fqn(class_index, target_class)
            if target_fqn and caller_class_fqn and caller_class_fqn != target_fqn:
                uses_edges.append(UsesEdge(from_fqn=caller_class_fqn, to_fqn=target_fqn))

        for dep in dependencies:
            target_class = dep.get("targetClass") if isinstance(dep, dict) else None
            if not target_class or not caller_class_fqn:
                continue
            target_fqn = _resolve_target_fqn(class_index, target_class)
            if target_fqn and caller_class_fqn != target_fqn:
                uses_edges.append(UsesEdge(from_fqn=caller_class_fqn, to_fqn=target_fqn))

    # 중복 제거 (같은 (from, to) 여러 번 나올 수 있음)
    seen_uses: set[Tuple[str, str]] = set()
    unique_uses: List[UsesEdge] = []
    for e in uses_edges:
        k = (e.from_fqn, e.to_fqn)
        if k not in seen_uses:
            seen_uses.add(k)
            unique_uses.append(e)

    logger.info("[FW:CALL_RESOLVE] 완료 | calls_edges=%d uses_edges=%d unique_uses=%d", len(calls_edges), len(uses_edges), len(unique_uses))
    return calls_edges, unique_uses


# call_code에서 receiver.methodName( 휴리스틱 추출
OBJ_METHOD_PATTERN = re.compile(r"(\w+)\s*\.\s*(\w+)\s*\(")
# 객체 없이 methodName( 만 있는 경우 (같은 클래스 내부 호출)
BARE_METHOD_PATTERN = re.compile(r"(?:^|=\s*)(\w+)\s*\(")

def _load_java_stdlib() -> frozenset:
    """java_stdlib.json에서 Java 표준 라이브러리 클래스명 로드."""
    data_path = Path(__file__).resolve().parent.parent / "internal" / "java_stdlib.json"
    try:
        with open(data_path, encoding="utf-8") as f:
            return frozenset(json.load(f))
    except Exception as e:
        logger.warning("[FW:CALL_BATCH] java_stdlib.json 로드 실패, 빈 set 사용: %s", e)
        return frozenset()

JAVA_STDLIB_CLASSES = _load_java_stdlib()


def load_call_records_from_chunk_bundles(
    data_dir: Optional[str] = None,
    chunk_bundle_dir: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """ChunkBundle에서 method_id별 call_records [{call_id, call_code}] 로드.
    chunk_bundle_dir 있으면 해당 디렉터리의 chunk_bundles 사용, 없으면 data_dir/chunk_bundles."""
    from analyzer.strategy.framework.parsing.chunk_bundle import (
        _get_chunk_bundle_dir,
        load_chunk_bundle,
    )
    base = Path(chunk_bundle_dir) if chunk_bundle_dir else Path(_get_chunk_bundle_dir(data_dir or ""))
    if not base.is_dir():
        logger.info("[FW:CALL_BATCH] ChunkBundle 로드 스킵 | reason=chunk_bundle_dir_not_found path=%s", base)
        return {}
    call_records_by_method: Dict[str, List[Dict[str, Any]]] = {}
    for path in base.glob("*.json"):
        try:
            bundle = load_chunk_bundle(path)
            for c in bundle.method_calls:
                call_records_by_method.setdefault(c.parent_method_id, []).append({
                    "call_id": c.call_id,
                    "call_code": c.call_code,
                })
            for v in bundle.variables:
                if not v.flags or not v.flags.get("initializerContainsMethodCall"):
                    continue
                method_id = v.parent_method_id
                recs = call_records_by_method.setdefault(method_id, [])
                seq = len(recs)
                call_id = f"C:{v.start_line}:{v.end_line}:{seq}"
                recs.append({
                    "call_id": call_id,
                    "call_code": (v.initializer_code or "").strip(),
                })
        except Exception as e:
            logger.debug("ChunkBundle 로드 스킵 %s: %s", path, e)
    total_records = sum(len(recs) for recs in call_records_by_method.values())
    logger.info(
        "[FW:CALL_BATCH] ChunkBundle 로드 완료 | methods_with_calls=%d total_records=%d",
        len(call_records_by_method),
        total_records,
    )
    return call_records_by_method


def extract_obj_method_from_call_code(call_code: str) -> Tuple[Optional[str], Optional[str]]:
    """call_code에서 objName, methodName 휴리스틱 추출. LLM 없을 때 fallback."""
    if not call_code:
        return None, None
    m = OBJ_METHOD_PATTERN.search(call_code)
    if m:
        return m.group(1), m.group(2)
    # 객체 프리픽스 없는 bare 호출 → this 호출로 간주
    bare = BARE_METHOD_PATTERN.search(call_code)
    if bare:
        method_name = bare.group(1)
        # Java 키워드/표준 생성자는 제외
        if method_name not in ("new", "return", "throw", "if", "while", "for", "switch", "try", "catch"):
            return "this", method_name
    return None, None


def _call_item_tokens(method_id: str, calls: List[Dict[str, Any]]) -> int:
    """한 (method_id, calls) 항목의 토큰 수 근사 (배치 묶기용)."""
    t = estimate_tokens(method_id or "")
    for c in calls or []:
        t += estimate_tokens(str(c.get("call_code") or ""))
    return max(1, t)


def _build_call_batches(
    call_records_by_method: Dict[str, List[Dict[str, Any]]],
    max_tokens: int,
) -> List[List[Tuple[str, List[Dict[str, Any]]]]]:
    """method_id별 call 목록을 max_tokens 이하로 묶어 배치 리스트 반환. 각 배치는 [(method_id, calls), ...]."""
    batches: List[List[Tuple[str, List[Dict[str, Any]]]]] = []
    current: List[Tuple[str, List[Dict[str, Any]]]] = []
    current_tokens = 0
    for method_id, recs in call_records_by_method.items():
        calls_input = [{"call_id": r.get("call_id", ""), "call_code": r.get("call_code", "")} for r in recs]
        t = _call_item_tokens(method_id, calls_input)
        if t > max_tokens:
            t = max_tokens
        if current and current_tokens + t > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((method_id, calls_input))
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _extract_calls_batch_via_llm(
    batch: List[Tuple[str, List[Dict[str, Any]]]],
    api_key: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """한 배치(여러 method_id + calls)를 한 번에 LLM에 보내 obj_name/method_name 추출. method_id -> results 반환."""
    if not batch or not api_key:
        return {}
    items = [{"method_id": mid, "calls": calls} for mid, calls in batch]
    try:
        loader = get_rule_loader("framework")
        result = loader.execute("call_extract", {"items": items}, api_key=api_key)
    except Exception as e:
        logger.warning("[FW:CALL_BATCH] LLM call_extract 오류: %s", e)
        return {mid: [] for mid, _ in batch}
    out: Dict[str, List[Dict[str, Any]]] = {}
    results_list = result.get("results") or []
    for i, (method_id, _) in enumerate(batch):
        if i < len(results_list) and isinstance(results_list[i], dict):
            res = results_list[i]
            out[method_id] = list(res.get("results") or [])
        else:
            out[method_id] = []
    for mid, _ in batch:
        if mid not in out:
            out[mid] = []
    logger.info(
        "[FW:CALL_BATCH] LLM call_extract 완료 | 배치 메서드 수=%d",
        len(batch),
    )
    return out


def _is_stdlib_class(name: Optional[str]) -> bool:
    """Java 표준/기본 라이브러리 클래스면 True. LLM 방어 로직용."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip()
    if not n:
        return False
    return n in JAVA_STDLIB_CLASSES or n.split(".")[-1] in JAVA_STDLIB_CLASSES


def _candidates_for_class_and_method(
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    class_name: str,
    method_name: str,
) -> List[str]:
    """FQN 또는 short name으로 후보 조회. method_index_by_class 키가 FQN이므로 short name이면 끝이 일치하는 키 모두 검사."""
    direct = (method_index_by_class.get(class_name) or {}).get(method_name, [])
    if direct:
        return direct
    # short name: 키가 ".ClassName"으로 끝나는 경우 수집
    out: List[str] = []
    for fqn, by_method in method_index_by_class.items():
        if fqn == class_name or fqn.endswith("." + class_name):
            out.extend(by_method.get(method_name, []))
    return out


def resolve_calls_deterministic(
    call_records_with_obj_method: List[Dict[str, Any]],  # [{call_id, objName, methodName, parent_method_id}]
    type_map: TypeMap3Layer,
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    method_to_class: Dict[str, str],
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """objName→className (type_map), method_index_by_class[className][methodName] → callee. 1개면 CALLS, 아니면 unresolved."""
    edges: List[CallsEdge] = []
    unresolved: List[Dict[str, Any]] = []
    for r in call_records_with_obj_method:
        caller_id = r.get("parent_method_id")
        obj_name = r.get("objName")
        method_name = r.get("methodName")
        if not caller_id or not method_name:
            logger.debug("[FW:CALL_BATCH] resolve 스킵 | reason=missing_caller_or_method call_id=%s", r.get("call_id"))
            continue
        if obj_name:
            className = resolve_obj_to_type(caller_id, obj_name, type_map, method_to_class)
        else:
            className = method_to_class.get(caller_id)  # this 호출로 간주
        if not className:
            unresolved.append(dict(r, className=None))  # external 시 obj_name으로 표시
            logger.debug("[FW:CALL_BATCH] resolve 스킵 | reason=obj_not_resolved obj_name=%s caller_id=%s", obj_name, caller_id)
            continue
        candidates = _candidates_for_class_and_method(method_index_by_class, className, method_name)
        if len(candidates) == 1:
            edges.append(CallsEdge(caller_method_id=caller_id, callee_method_id=candidates[0], call_code=r.get("call_code", ""), scope="internal"))
        else:
            r_with_class = dict(r)
            r_with_class["className"] = className  # external 표시용
            unresolved.append(r_with_class)
            logger.debug(
                "[FW:CALL_BATCH] resolve 스킵 | reason=ambiguous_or_none candidates=%d className=%s method_name=%s",
                len(candidates),
                className,
                method_name,
            )
    logger.info("[FW:CALL_BATCH] deterministic 완료 | edges=%d unresolved=%d", len(edges), len(unresolved))
    return edges, unresolved


def build_external_calls_edges(
    unresolved: List[Dict[str, Any]],
    class_map: Dict[Tuple[str, str], Tuple[str, str]],
) -> List[CallsEdge]:
    """클래스맵에 있고 scope=external인 경우만 external CALLS 엣지 생성.
    클래스맵에 존재하지 않는 (객체명, 타입) → 라이브러리/표준 메서드로 간주하여 CALLS 생성하지 않음."""
    external: List[CallsEdge] = []
    for r in unresolved or []:
        caller_id = r.get("parent_method_id")
        obj_name = r.get("objName") or ""
        method_name = r.get("methodName") or "?"
        if not caller_id or not obj_name:
            continue
        key = (caller_id, obj_name)
        if key not in class_map:
            # 클래스맵에 없음 = 라이브러리/표준 → CALLS 생성 안 함
            continue
        class_name, scope = class_map[key]
        if scope != "external":
            continue
        if _is_stdlib_class(class_name):
            continue
        callee_id = f"external:{class_name}#{method_name}"
        external.append(CallsEdge(
            caller_method_id=caller_id,
            callee_method_id=callee_id,
            call_code=r.get("call_code", ""),
            scope="external",
        ))
    if external:
        logger.info("[FW:CALL_BATCH] external CALLS 엣지 생성 | count=%d", len(external))
    return external


def _external_callee_class(callee_method_id: str) -> Optional[str]:
    """external:ClassName#methodName → ClassName. 아니면 None."""
    s = (callee_method_id or "").strip()
    if not s.startswith("external:"):
        return None
    rest = s[len("external:"):]
    if "#" in rest:
        return rest.split("#", 1)[0].strip() or None
    return rest.strip() or None


def derive_uses_from_calls(
    calls_edges: List[CallsEdge],
    method_facts_list: List[Any],
) -> List[UsesEdge]:
    """CallsEdge 리스트에서 (caller_class_fqn, callee_class_fqn) 추출 → UsesEdge. internal·external 모두 포함(framework와 동일)."""
    method_id_to_class_fqn: Dict[str, str] = {}
    for m in method_facts_list or []:
        mid = getattr(m, "method_id", None)
        if not mid:
            continue
        fqn = getattr(m, "fqn", None) or mid
        class_fqn = fqn.rsplit(".", 1)[0] if "." in fqn else fqn
        method_id_to_class_fqn[mid] = class_fqn
    seen: set[Tuple[str, str, str]] = set()  # (from, to, scope)
    out: List[UsesEdge] = []
    for e in calls_edges or []:
        caller_cfqn = method_id_to_class_fqn.get(e.caller_method_id) or (
            e.caller_method_id.rsplit(".", 1)[0] if "." in e.caller_method_id else ""
        )
        if not caller_cfqn:
            continue
        is_external = getattr(e, "scope", "internal") == "external" or (getattr(e, "callee_method_id", "") or "").startswith("external:")
        if is_external:
            callee_cfqn = _external_callee_class(e.callee_method_id)
            scope = "external"
        else:
            callee_cfqn = method_id_to_class_fqn.get(e.callee_method_id) or (
                e.callee_method_id.rsplit(".", 1)[0] if "." in e.callee_method_id else ""
            )
            scope = "internal"
        if not callee_cfqn or caller_cfqn == callee_cfqn:
            continue
        key = (caller_cfqn, callee_cfqn, scope)
        if key in seen:
            continue
        seen.add(key)
        out.append(UsesEdge(from_fqn=caller_cfqn, to_fqn=callee_cfqn, scope=scope))
    logger.info("[FW:CALL_BATCH] derive_uses_from_calls | calls=%d -> uses=%d", len(calls_edges or []), len(out))
    return out


def _flatten_one_batch(
    batch: List[Tuple[str, List[Dict[str, Any]]]],
    llm_results: Dict[str, List[Dict[str, Any]]],
    call_records_by_method: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """한 배치의 LLM 결과를 flat 레코드로 변환."""
    flat: List[Dict[str, Any]] = []
    for method_id, _calls_input in batch:
        by_call_id = {
            r.get("call_id"): r
            for r in (llm_results.get(method_id) or [])
            if r.get("call_id")
        }
        for r in call_records_by_method.get(method_id, []):
            cid = r.get("call_id", "")
            info = by_call_id.get(cid, {})
            obj_name = info.get("obj_name") or None
            method_name = info.get("method_name") or None
            if obj_name and _is_stdlib_class(obj_name):
                obj_name, method_name = "", ""
            flat.append({
                "parent_method_id": method_id,
                "call_id": cid,
                "call_code": r.get("call_code", ""),
                "objName": obj_name,
                "methodName": method_name,
            })
    return flat


def run_call_batch_pipeline(
    data_dir: str,
    method_facts_list: List[Any],
    field_facts_list: List[Any],
    symbol_type_index: Dict[str, Dict[str, str]],
    method_index_by_class: Dict[str, Dict[str, List[str]]],
    use_llm: bool = True,
    api_key: Optional[str] = None,
    run_dir: Optional[str] = None,
    on_batch: Optional[Any] = None,
) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
    """CallBatch 로드 → objName/methodName 추출(LLM 필수 시 RuleLoader, 없으면 휴리스틱) → Deterministic Link.
    on_batch 콜백: 각 LLM 배치 완료 시 (edges, unresolved) 전달.
    run_dir 있으면 해당 run_dir/chunk_bundles에서 ChunkBundle을 로드."""
    from analyzer.strategy.framework.parsing.chunk_bundle import _get_chunk_bundle_dir_from_run_dir
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    call_records_by_method = load_call_records_from_chunk_bundles(
        data_dir=data_dir,
        chunk_bundle_dir=chunk_dir,
    )
    if not call_records_by_method:
        logger.info("[FW:CALL_BATCH] 파이프라인 스킵 | reason=no_call_records chunk_dir=%s", chunk_dir or data_dir)
        return [], []

    logger.info(
        "[FW:CALL_BATCH] 파이프라인 진입 | methods_with_calls=%d use_llm=%s api_key_set=%s",
        len(call_records_by_method),
        use_llm,
        bool(api_key),
    )
    type_map = build_type_map_3layer(symbol_type_index, field_facts_list, method_facts_list)
    m2c = method_to_class_map(method_facts_list)
    internal_class_fqns = set(method_index_by_class.keys())
    is_domain_class = lambda cn: bool(
        cn and str(cn).strip() and str(cn).strip()[0].isupper() and not _is_stdlib_class(str(cn).strip())
    )
    class_map = build_class_map_with_scope(type_map, m2c, internal_class_fqns, is_domain_class)

    all_edges: List[CallsEdge] = []
    all_unresolved: List[Dict[str, Any]] = []
    key = api_key or ""

    if use_llm and key:
        max_tokens = settings.batch.max_batch_token * 2
        batches = _build_call_batches(call_records_by_method, max_tokens)
        logger.info(
            "[FW:CALL_BATCH] 청크 배치 | methods=%d → 배치=%d (max_tokens=%d)",
            len(call_records_by_method),
            len(batches),
            max_tokens,
        )
        workers = settings.concurrency.max_concurrency

        def _process_and_resolve(batch: List[Tuple[str, List[Dict[str, Any]]]]) -> Tuple[List[CallsEdge], List[Dict[str, Any]]]:
            """한 배치: LLM 추출 → flatten → resolve → external edges."""
            llm_out = _extract_calls_batch_via_llm(batch, key)
            batch_flat = _flatten_one_batch(batch, llm_out, call_records_by_method)
            batch_edges, batch_unresolved = resolve_calls_deterministic(batch_flat, type_map, method_index_by_class, m2c)
            batch_external = build_external_calls_edges(batch_unresolved, class_map)
            return batch_edges + batch_external, batch_unresolved

        if workers <= 1:
            for batch in batches:
                batch_edges, batch_unresolved = _process_and_resolve(batch)
                all_edges.extend(batch_edges)
                all_unresolved.extend(batch_unresolved)
                if on_batch and batch_edges:
                    on_batch(batch_edges, batch_unresolved)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_process_and_resolve, batch): batch for batch in batches}
                for future in as_completed(futures):
                    try:
                        batch_edges, batch_unresolved = future.result()
                        all_edges.extend(batch_edges)
                        all_unresolved.extend(batch_unresolved)
                        if on_batch and batch_edges:
                            on_batch(batch_edges, batch_unresolved)
                    except Exception as ex:
                        logger.warning("[FW:CALL_BATCH] 배치 LLM 처리 예외: %s", ex)
    else:
        flat: List[Dict[str, Any]] = []
        for method_id, recs in call_records_by_method.items():
            for r in recs:
                call_code = r.get("call_code", "")
                obj_name, method_name = extract_obj_method_from_call_code(call_code)
                if obj_name and _is_stdlib_class(obj_name):
                    obj_name, method_name = None, None
                flat.append({
                    "parent_method_id": method_id,
                    "call_id": r.get("call_id", ""),
                    "call_code": call_code,
                    "objName": obj_name,
                    "methodName": method_name,
                })
        edges, unresolved = resolve_calls_deterministic(flat, type_map, method_index_by_class, m2c)
        external_edges = build_external_calls_edges(unresolved, class_map)
        all_edges = edges + external_edges
        all_unresolved = unresolved
        if on_batch and all_edges:
            on_batch(all_edges, all_unresolved)

    logger.info(
        "[FW:CALL_BATCH] 파이프라인 완료 | edges=%d unresolved=%d",
        len(all_edges),
        len(all_unresolved),
    )
    return all_edges, all_unresolved
