"""타입/클래스 매핑 Map 3층: method_scope, class_scope, runtime_hint. objName→className 결정론 해석용."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MethodScopeMap:
    """메서드 스코프: local var → type, param → type, initializerContainsMethodCall 결과 타입."""
    local: Dict[str, str] = field(default_factory=dict)   # varName -> type
    param: Dict[str, str] = field(default_factory=dict)    # paramName -> type
    call_result_type: Dict[str, str] = field(default_factory=dict)  # lhsVarName -> type (LLM/추론)


@dataclass
class ClassScopeMap:
    """클래스 스코프: field → type (CLASS/FIELD 노드 기반)."""
    field: Dict[str, str] = field(default_factory=dict)   # fieldName -> type


@dataclass
class RuntimeHintMap:
    """NEW_INSTANCE 기반 var → 구현체 타입 (인터페이스→구현체 확정)."""
    var_to_impl: Dict[str, str] = field(default_factory=dict)  # varName -> implClassName


@dataclass
class TypeMap3Layer:
    """메서드별 3층 맵. method_id -> (method_scope, class_scope, runtime_hint)."""
    method_scope: Dict[str, MethodScopeMap] = field(default_factory=dict)   # method_id -> MethodScopeMap
    class_scope: Dict[str, ClassScopeMap] = field(default_factory=dict)     # class_key -> ClassScopeMap
    runtime_hint: Dict[str, RuntimeHintMap] = field(default_factory=dict)    # method_id -> RuntimeHintMap


def resolve_obj_to_type(
    method_id: str,
    obj_name: str,
    type_map: TypeMap3Layer,
    method_to_class: Dict[str, str],  # method_id -> class_key (FQN)
) -> Optional[str]:
    """objName → className/FQN 결정. 우선순위: this → runtime_hint → local → param → field → 대문자 휴리스틱."""
    if not obj_name:
        return None
    # 0. this → 호출자 메서드의 클래스 FQN
    if obj_name.strip() == "this" and method_id:
        return method_to_class.get(method_id)
    # 1. runtime_hint (NEW_INSTANCE로 확정된 구현체)
    rh = type_map.runtime_hint.get(method_id)
    if rh and obj_name in rh.var_to_impl:
        return rh.var_to_impl[obj_name]
    # 2. method_scope.local
    ms = type_map.method_scope.get(method_id)
    if ms and obj_name in ms.local:
        return ms.local[obj_name]
    # 3. method_scope.param
    if ms and obj_name in ms.param:
        return ms.param[obj_name]
    # 4. method_scope.call_result_type (LLM이 lhsVarName 반환 시)
    if ms and obj_name in ms.call_result_type:
        return ms.call_result_type[obj_name]
    # 5. class_scope.field
    class_key = method_to_class.get(method_id)
    if class_key:
        cs = type_map.class_scope.get(class_key)
        if cs and obj_name in cs.field:
            return cs.field[obj_name]
    # 6. 대문자로 시작하면 ClassName 휴리스틱
    if obj_name and obj_name[0].isupper():
        return obj_name
    return None


def build_type_map_3layer(
    symbol_type_index: Dict[str, Dict[str, str]],  # method_id -> {varName: type}
    field_facts_list: List[Any],  # FieldFacts with class_key, name, field_type
    method_facts_list: List[Any],  # MethodFacts with method_id, class_key, params
    runtime_hint_by_method: Optional[Dict[str, Dict[str, str]]] = None,  # method_id -> {varName: implType}
) -> TypeMap3Layer:
    """symbol_type_index, FIELD, METHOD params로 3층 맵 구성. JSON 직렬화 가능."""
    type_map = TypeMap3Layer()
    # method_scope: symbol_type_index를 local로 사용
    for method_id, var_types in symbol_type_index.items():
        ms = MethodScopeMap(local=dict(var_types))
        type_map.method_scope[method_id] = ms
    # params from method_facts (간단 파싱: "Type name, Type2 name2" -> param name -> type 휴리스틱)
    for m in method_facts_list:
        method_id = getattr(m, "method_id", None)
        if not method_id:
            continue
        params_str = getattr(m, "params", None) or ""
        if method_id not in type_map.method_scope:
            type_map.method_scope[method_id] = MethodScopeMap()
        ms = type_map.method_scope[method_id]
        for part in params_str.split(","):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            if len(tokens) >= 2:
                type_name = tokens[-2]
                param_name = tokens[-1].rstrip(")")
                ms.param[param_name] = type_name
    # class_scope: field_facts
    for f in field_facts_list:
        class_key = getattr(f, "class_key", None)
        name = getattr(f, "name", None)
        field_type = getattr(f, "field_type", None)
        if not class_key or not name:
            continue
        if class_key not in type_map.class_scope:
            type_map.class_scope[class_key] = ClassScopeMap()
        type_map.class_scope[class_key].field[name] = field_type or ""
    # runtime_hint
    if runtime_hint_by_method:
        for method_id, var_impl in runtime_hint_by_method.items():
            type_map.runtime_hint[method_id] = RuntimeHintMap(var_to_impl=dict(var_impl))
    return type_map


def method_to_class_map(method_facts_list: List[Any]) -> Dict[str, str]:
    """method_id -> class_key."""
    return {getattr(m, "method_id", ""): getattr(m, "class_key", "") or "" for m in method_facts_list if getattr(m, "method_id", None)}


def build_class_map_with_scope(
    type_map: TypeMap3Layer,
    method_to_class: Dict[str, str],
    internal_class_fqns: Any,  # set of class FQN or class_key that exist in AST
    is_domain_class: Any,  # callable(class_name) -> bool, False for primitive/stdlib
) -> Dict[tuple, tuple]:
    """(method_id, obj_name) -> (class_name, scope). scope = 'internal' | 'external'.
    internal_class_fqns: set of class FQN (or short names) that exist in our AST (e.g. method_index_by_class.keys()).
    is_domain_class: returns False for primitive/stdlib so we exclude them from the map."""
    out: Dict[tuple, tuple] = {}
    if not isinstance(internal_class_fqns, set):
        internal_class_fqns = set(internal_class_fqns or [])

    for method_id, ms in type_map.method_scope.items():
        class_key = method_to_class.get(method_id)
        for obj_name, type_name in list(ms.local.items()) + list(ms.param.items()):
            if not type_name or not is_domain_class(type_name):
                continue
            scope = "internal" if _class_in_set(type_name, internal_class_fqns) else "external"
            out[(method_id, obj_name)] = (type_name, scope)
        if class_key and method_id:
            out[(method_id, "this")] = (class_key, "internal" if class_key in internal_class_fqns else "external")

    for class_key, cs in type_map.class_scope.items():
        for field_name, field_type in cs.field.items():
            if not field_type or not is_domain_class(field_type):
                continue
            scope = "internal" if _class_in_set(field_type, internal_class_fqns) else "external"
            for method_id, m_class in method_to_class.items():
                if m_class == class_key:
                    out[(method_id, field_name)] = (field_type, scope)
                    break
    return out


def _class_in_set(class_name: str, internal_class_fqns: set) -> bool:
    """Check if class_name is in internal set (FQN or short name match)."""
    if not class_name:
        return False
    if class_name in internal_class_fqns:
        return True
    short = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name
    return any(fqn == class_name or fqn.endswith("." + short) for fqn in internal_class_fqns)
