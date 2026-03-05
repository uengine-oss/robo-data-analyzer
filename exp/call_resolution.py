"""LLM analysis.calls / dependencies → CallsEdge, UsesEdge 해상도. class_index·method_facts_list 사용."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from exp.models import CallsEdge, MethodFacts, UsesEdge

logger = logging.getLogger(__name__)


def build_param_type_map_from_params(params: str) -> Dict[str, str]:
    """parameters 문자열에서 paramName → paramType 매핑 (uml_relations.build_param_type_map 동일 로직)."""
    param_map: Dict[str, str] = {}
    if not params or not isinstance(params, str):
        return param_map
    raw = params.strip()
    if not raw:
        return param_map
    for part in raw.split(","):
        seg = part.strip()
        if not seg:
            continue
        seg = seg.replace("(", " ").replace(")", " ")
        tokens = [t for t in seg.split() if t]
        if len(tokens) < 2:
            continue
        param_name = tokens[-1]
        type_tokens = tokens[:-1]
        param_type = type_tokens[-1]
        if param_name in param_map and param_map[param_name] != param_type:
            continue
        param_map[param_name] = param_type
    return param_map


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
    logger.info("[EXP:CALL_RESOLVE] 진입 | summary_methods=%d", len(summary_by_method))
    calls_edges: List[CallsEdge] = []
    uses_edges: List[UsesEdge] = []
    method_by_id: Dict[str, MethodFacts] = {m.method_id: m for m in method_facts_list}

    for method_id, data in summary_by_method.items():
        calls = data.get("calls") or []
        dependencies = data.get("dependencies") or []
        caller = method_by_id.get(method_id)
        if not caller:
            logger.info("[EXP:CALL_RESOLVE] 스킵 | reason=caller_not_found method_id=%s", method_id)
            continue
        caller_class_fqn = method_id.rsplit(".", 1)[0] if "." in method_id else ""
        param_type_map = build_param_type_map_from_params(caller.params or "")
        field_map = build_field_map_for_class(field_facts_list, caller.class_key or "")
        local_type_map: Dict[str, str] = {}

        for call_expr in calls:
            if not isinstance(call_expr, str) or "." not in call_expr:
                logger.info("[EXP:CALL_RESOLVE] 스킵 | reason=invalid_call_expr method_id=%s call=%s", method_id, call_expr)
                continue
            parts = call_expr.split(".", 1)
            object_name = parts[0].strip()
            method_name = (parts[1].strip() if len(parts) > 1 else "") or ""
            if not object_name or not method_name:
                logger.info("[EXP:CALL_RESOLVE] 스킵 | reason=missing_obj_or_method method_id=%s", method_id)
                continue
            target_class = (
                local_type_map.get(object_name)
                or param_type_map.get(object_name)
                or field_map.get(object_name)
            )
            if not target_class and object_name and object_name[0].isupper():
                target_class = object_name
            if not target_class:
                logger.info("[EXP:CALL_RESOLVE] 스킵 | reason=target_class_unknown object_name=%s method_id=%s", object_name, method_id)
                continue
            callee_method_id = _find_callee_method_id(method_facts_list, class_index, target_class, method_name)
            if not callee_method_id:
                logger.info("[EXP:CALL_RESOLVE] 스킵 | reason=callee_not_found target_class=%s method_name=%s", target_class, method_name)
            if callee_method_id:
                calls_edges.append(CallsEdge(caller_method_id=method_id, callee_method_id=callee_method_id, call_code=call_expr))
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

    logger.info("[EXP:CALL_RESOLVE] 완료 | calls_edges=%d uses_edges=%d unique_uses=%d", len(calls_edges), len(uses_edges), len(unique_uses))
    return calls_edges, unique_uses
