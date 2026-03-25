# 타입/모듈 매핑 Map 3층: method_scope, module_scope, runtime_hint. objName→moduleName 결정론 해석용.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from util.text_utils import parse_params


@dataclass
class MethodScopeMap:
    """메서드 스코프: local var -> type, param -> type, initializerContainsMethodCall 결과 타입."""
    local: Dict[str, str] = field(default_factory=dict)   # varName -> type
    param: Dict[str, str] = field(default_factory=dict)    # paramName -> type
    call_result_type: Dict[str, str] = field(default_factory=dict)  # lhsVarName -> type (LLM/추론)


@dataclass
class ModuleScopeMap:
    """모듈 스코프: variable -> type (MODULE/VARIABLE 노드 기반)."""
    variable: Dict[str, str] = field(default_factory=dict)   # variableName -> type


@dataclass
class RuntimeHintMap:
    """NEW_INSTANCE 기반 var -> 구현체 타입 (인터페이스->구현체 확정)."""
    var_to_impl: Dict[str, str] = field(default_factory=dict)  # varName -> implModuleName


@dataclass
class TypeMap3Layer:
    """메서드별 3층 맵. function_id -> (method_scope, module_scope, runtime_hint)."""
    method_scope: Dict[str, MethodScopeMap] = field(default_factory=dict)   # function_id -> MethodScopeMap
    module_scope: Dict[str, ModuleScopeMap] = field(default_factory=dict)   # module_key -> ModuleScopeMap
    runtime_hint: Dict[str, RuntimeHintMap] = field(default_factory=dict)    # function_id -> RuntimeHintMap


def resolve_obj_to_type(
    function_id: str,
    obj_name: str,
    type_map: TypeMap3Layer,
    method_to_module: Dict[str, str],  # function_id -> module_key (FQN)
) -> Optional[str]:
    """objName -> moduleName/FQN 결정. 우선순위: this -> runtime_hint -> local -> param -> variable -> 대문자 휴리스틱."""
    if not obj_name:
        return None
    # 0. this -> 호출자 메서드의 모듈 FQN
    if obj_name.strip() == "this" and function_id:
        return method_to_module.get(function_id)
    # 1. runtime_hint (NEW_INSTANCE로 확정된 구현체)
    rh = type_map.runtime_hint.get(function_id)
    if rh and obj_name in rh.var_to_impl:
        return rh.var_to_impl[obj_name]
    # 2. method_scope.local
    ms = type_map.method_scope.get(function_id)
    if ms and obj_name in ms.local:
        return ms.local[obj_name]
    # 3. method_scope.param
    if ms and obj_name in ms.param:
        return ms.param[obj_name]
    # 4. method_scope.call_result_type (LLM이 lhsVarName 반환 시)
    if ms and obj_name in ms.call_result_type:
        return ms.call_result_type[obj_name]
    # 5. module_scope.variable
    module_key = method_to_module.get(function_id)
    if module_key:
        cs = type_map.module_scope.get(module_key)
        if cs and obj_name in cs.variable:
            return cs.variable[obj_name]
    # 6. 대문자로 시작하면 ModuleName 휴리스틱
    if obj_name and obj_name[0].isupper():
        return obj_name
    return None


_LANG_PARAM_STYLE = {
    "python": "name_colon_type",
    "kotlin": "name_colon_type",
    "go": "name_type",
}


def build_type_map_3layer(
    symbol_type_index: Dict[str, Dict[str, str]],  # function_id -> {varName: type}
    variable_facts_list: List[Any],  # VariableFacts with module_key, name, variable_type
    function_facts_list: List[Any],  # FunctionFacts with function_id, module_key, params
    runtime_hint_by_function: Optional[Dict[str, Dict[str, str]]] = None,  # function_id -> {varName: implType}
    lang: str = "",  # 언어 코드 (python, java, c, go 등)
) -> TypeMap3Layer:
    """symbol_type_index, VARIABLE, FUNCTION params로 3층 맵 구성. JSON 직렬화 가능."""
    param_style = _LANG_PARAM_STYLE.get(lang, "type_name")
    type_map = TypeMap3Layer()
    # method_scope: symbol_type_index를 local로 사용
    for function_id, var_types in symbol_type_index.items():
        ms = MethodScopeMap(local=dict(var_types))
        type_map.method_scope[function_id] = ms
    # params from function_facts (언어별 파싱 스타일 적용)
    for m in function_facts_list:
        function_id = getattr(m, "function_id", None)
        if not function_id:
            continue
        params_str = getattr(m, "params", None) or ""
        if function_id not in type_map.method_scope:
            type_map.method_scope[function_id] = MethodScopeMap()
        ms = type_map.method_scope[function_id]
        ms.param.update(parse_params(params_str, param_style))
    # module_scope: variable_facts
    for f in variable_facts_list:
        module_key = getattr(f, "module_key", None)
        variable_name = getattr(f, "name", None)
        variable_type = getattr(f, "variable_type", None)
        if not module_key or not variable_name:
            continue
        if module_key not in type_map.module_scope:
            type_map.module_scope[module_key] = ModuleScopeMap()
        type_map.module_scope[module_key].variable[variable_name] = variable_type or ""
    # runtime_hint
    if runtime_hint_by_function:
        for function_id, var_impl in runtime_hint_by_function.items():
            type_map.runtime_hint[function_id] = RuntimeHintMap(var_to_impl=dict(var_impl))
    return type_map


def method_to_module_map(function_facts_list: List[Any]) -> Dict[str, str]:
    """function_id -> module_key."""
    return {getattr(m, "function_id", ""): getattr(m, "module_key", "") for m in function_facts_list if getattr(m, "function_id", None)}


def build_module_map_with_scope(
    type_map: TypeMap3Layer,
    method_to_module: Dict[str, str],
    internal_module_fqns: Any,  # set of module FQN or module_key that exist in AST
    is_domain_module: Any,  # callable(module_name) -> bool, False for primitive/stdlib
) -> Dict[tuple, tuple]:
    """(function_id, obj_name) -> (module_name, scope). scope = 'internal' | 'external'.
    internal_module_fqns: set of module FQN (or short names) that exist in our AST (e.g. method_index_by_module.keys()).
    is_domain_module: returns False for primitive/stdlib so we exclude them from the map."""
    out: Dict[tuple, tuple] = {}
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
            out[(function_id, "this")] = (module_key, "internal" if module_key in internal_module_fqns else "external")

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
    """Check if module_name is in internal set (FQN or short name match)."""
    if not module_name:
        return False
    if module_name in internal_module_fqns:
        return True
    short = module_name.rsplit(".", 1)[-1] if "." in module_name else module_name
    return any(fqn == module_name or fqn.endswith("." + short) for fqn in internal_module_fqns)
