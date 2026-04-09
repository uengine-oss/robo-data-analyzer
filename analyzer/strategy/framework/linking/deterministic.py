"""결정론적 매칭 — 타입 추론 + 인덱스 매칭 + 후보 해석.

기존 type_resolver.py + matcher.py 통합.
타입 추론 (TypeMap3Layer, resolve_obj_to_type) + 인덱스 매칭 (link_chunks_to_indexes, resolve_candidates)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

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
    # 6. 해결 불가 — None 반환 (호출부에서 unresolved 처리)
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


def fact_to_module_map(function_facts_list: List[Any]) -> Dict[str, str]:
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


# ═══════════════════════════════════════════════════════════════════════
# 인덱스 매칭 + 후보 해석 (구 matcher.py)
# ═══════════════════════════════════════════════════════════════════════

from analyzer.strategy.framework.common.models import (
    FunctionFacts,
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
    ReferenceCandidate,
    ReferToEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.common.utils import (
    key_in_code,
)

logger = logging.getLogger(__name__)


# 엔티티 시그널 — 언어/프레임워크 무관 (어노테이션, 상속 패턴)
ENTITY_SIGNALS_ANNOTATION = [
    # JVM (Java, Kotlin, etc.)
    "@Entity", "@Table", "@MappedSuperclass", "@Document", "@RedisHash",
    # Python (Django, SQLAlchemy)
    "db.Model", "models.Model",
    # .NET (C#, etc.)
    "[Table", "[Key",
    # General
    "EntityBean",
]


def identify_entity_classes(module_index: Dict[str, Any]) -> set:
    """module_index에서 엔티티 후보 FQN 집합을 반환. 어노테이션/상속 패턴으로 판별 (범용)."""
    entity_fqns: set = set()
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        ann = str(info.get("annotations") or "")
        if any(s in ann for s in ENTITY_SIGNALS_ANNOTATION):
            entity_fqns.add(fqn)
    return entity_fqns


def has_external_entity_info(
    artifact_index: Optional[Dict[str, Any]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    ddl_table_metadata: Optional[Dict[str, Any]] = None,
    **extra_sources: Any,
) -> bool:
    """외부 소스에서 엔티티/테이블 정보가 이미 존재하는지 판별. 소스 종류를 한정하지 않음."""
    if ddl_table_metadata:
        return True
    if artifact_index:
        for ac in artifact_index.values():
            if getattr(ac, "chunk_type", "") in ("entity", "cmp_mapping"):
                return True
    if module_index and identify_entity_classes(module_index):
        return True
    for src in extra_sources.values():
        if src:
            return True
    return False







def link_chunks_to_indexes(
    function_facts_list: List[FunctionFacts],
    field_chunks: Optional[List[Dict[str, Any]]] = None,
    sql_index: Optional[Dict[str, SqlStatementChunk]] = None,
    config_index: Optional[Dict[str, ConfigSectionChunk]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """Method/Field 청크 코드에 인덱스 키가 있으면 REFER_TO 연결. 문자열 추출/패턴 없이 key in code 만 검사."""
    edges: List[ReferToEdge] = []
    unresolved: List[UnresolvedLink] = []
    seen: set = set()  # (source_id, source_kind, target_type, target_id)

    artifact_key_index: Dict[str, List[str]] = {}
    if artifact_index:
        for chunk_id, ac in artifact_index.items():
            keys: List[str] = []
            key_id = (chunk_id or "").strip()
            if key_id:
                keys.append(key_id)
            hint = getattr(ac, "hint_key", None)
            hint = (hint or "").strip()
            if hint:
                keys.append(hint)
            for k in keys:
                artifact_key_index.setdefault(k, []).append(key_id)

    def add_edge(function_id: str, variable_id: Optional[str], target_type: str, target_id: str, evidence: Dict[str, Any]) -> None:
        sid = variable_id if variable_id else function_id
        kind = "Variable" if variable_id else "Function"
        t = (sid, kind, target_type, target_id)
        if t in seen:
            return
        seen.add(t)
        edges.append(
            ReferToEdge(
                function_id=function_id,
                target_type=target_type,
                target_id=target_id,
                evidence=evidence,
                confidence=1.0,
                variable_id=variable_id,
            )
        )

    # 매칭된 인덱스 키 추적 (매칭 실패 키 → unresolved → deepagent)
    matched_keys: Set[Tuple[str, str]] = set()  # (target_type, key)

    for m in function_facts_list:
        code = (m.code or "").strip()
        if not code:
            continue
        evidence = {"file": m.code_ref, "line_range": [m.start_line, m.end_line]}
        if sql_index:
            for key in sql_index:
                if key_in_code(code, key):
                    add_edge(m.function_id, None, "SqlStatement", key, evidence)
                    matched_keys.add(("SqlStatement", key))
        if config_index:
            for key in config_index.keys():
                if key_in_code(code, key):
                    add_edge(m.function_id, None, "ConfigSection", key, evidence)
                    matched_keys.add(("ConfigSection", key))
        if artifact_key_index:
            for key, chunk_ids in artifact_key_index.items():
                if not key_in_code(code, key):
                    continue
                for cid in chunk_ids:
                    if cid:
                        add_edge(m.function_id, None, "ArtifactChunk", cid, evidence)
                        matched_keys.add(("ArtifactChunk", key))

    if field_chunks:
        for fc in field_chunks:
            variable_id = fc.get("variable_id") or ""
            code = (fc.get("code") or "").strip()
            if not variable_id or not code:
                continue
            evidence = {"file": fc.get("code_ref"), "line_range": fc.get("line_range")}
            if sql_index:
                for key in sql_index:
                    if key_in_code(code, key):
                        add_edge("", variable_id, "SqlStatement", key, evidence)
                        matched_keys.add(("SqlStatement", key))
            if config_index:
                for key in config_index.keys():
                    if key_in_code(code, key):
                        add_edge("", variable_id, "ConfigSection", key, evidence)
                        matched_keys.add(("ConfigSection", key))
            if artifact_key_index:
                for key, chunk_ids in artifact_key_index.items():
                    if not key_in_code(code, key):
                        continue
                    for cid in chunk_ids:
                        if cid:
                            add_edge("", variable_id, "ArtifactChunk", cid, evidence)
                            matched_keys.add(("ArtifactChunk", key))

    # 매칭 실패 인덱스 키 수집 → unresolved (deepagent 대상)
    # sql, config, artifact 인덱스 키 중 어떤 메서드/필드에도 매칭되지 않은 것
    if sql_index:
        for key, stmt in sql_index.items():
            if ("SqlStatement", key) not in matched_keys:
                unresolved.append(UnresolvedLink(
                    function_id="", caller_fqn="",
                    ref_candidates=[{
                        "target_type": "SqlStatement",
                        "key": key,
                        "category": "sql",
                        "sql_type": getattr(stmt, "sql_type", ""),
                        "file_path": getattr(stmt, "file_path", ""),
                    }],
                ))
    if config_index:
        for key, cfg in config_index.items():
            if ("ConfigSection", key) not in matched_keys:
                unresolved.append(UnresolvedLink(
                    function_id="", caller_fqn="",
                    ref_candidates=[{
                        "target_type": "ConfigSection",
                        "key": key,
                        "category": "config",
                        "file_path": getattr(cfg, "file_path", ""),
                    }],
                ))
    if artifact_index:
        for chunk_id, ac in artifact_index.items():
            hint = (getattr(ac, "hint_key", None) or "").strip()
            chunk_id_clean = (chunk_id or "").strip()
            if ("ArtifactChunk", chunk_id_clean) in matched_keys:
                continue
            if hint and ("ArtifactChunk", hint) in matched_keys:
                continue
            chunk_type = getattr(ac, "chunk_type", "")
            category = chunk_type if chunk_type else "artifact"
            # 원문 전달 (토큰 한도 내에서, 초과 시 잘라서)
            full_text = (getattr(ac, "text", "") or "").strip()
            _MAX_SNIPPET_CHARS = 6000  # ~2000 토큰 상당
            snippet = full_text if len(full_text) <= _MAX_SNIPPET_CHARS else full_text[:_MAX_SNIPPET_CHARS]
            unresolved.append(UnresolvedLink(
                function_id="", caller_fqn="",
                ref_candidates=[{
                    "target_type": "ArtifactChunk",
                    "key": hint or chunk_id_clean,
                    "chunk_id": chunk_id_clean,
                    "category": category,
                    "chunk_type": chunk_type,
                    "text_snippet": snippet,
                    "file_path": getattr(ac, "source_path", ""),
                }],
            ))

    logger.info(
        "[PHASE:2:LINKING] link_chunks_to_indexes 완료 | edges=%d unmatched_keys=%d",
        len(edges), len(unresolved),
    )
    return edges, unresolved


def resolve_candidates(
    function_id: str,
    caller_fqn: str,
    code_ref: Optional[str],
    candidates: List[Any],
    sql_index: Optional[Dict[str, SqlStatementChunk]],
    config_index: Optional[Dict[str, ConfigSectionChunk]],
    module_index: Optional[Dict[str, Any]],
    symbol_type_index: Optional[Dict[str, Dict[str, str]]],
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """AST에서 추출된 ref 후보를 결정론적으로 해석해 ReferToEdge 또는 UnresolvedLink 로 분류."""
    edges: List[ReferToEdge] = []
    unresolved: List[UnresolvedLink] = []
    sym_idx = symbol_type_index or {}
    module_idx = module_index or {}
    art_idx = artifact_index or {}

    def _as_dict(c: Any) -> Dict[str, Any]:
        if isinstance(c, dict):
            return c
        if isinstance(c, ReferenceCandidate):
            return {
                "kind": c.kind,
                "value": c.value,
                "receiver": c.receiver,
                "function_name": c.function_name,
                "moduleName": c.moduleName,
                "path": c.path,
                "line_range": c.line_range,
                "code_snippet": c.code_snippet,
            }
        return {"kind": getattr(c, "kind", ""), "value": getattr(c, "value", None)}

    for raw in candidates or []:
        c = _as_dict(raw)
        kind = c.get("kind", "") or ""
        value = c.get("value")
        target_type: Optional[str] = None
        target_id: Optional[str] = None

        # SQL / Config 키 직접 참조
        if kind == "SQL_KEY_LITERAL" and value and sql_index:
            if value in sql_index:
                target_type, target_id = "SqlStatement", value
            else:
                unresolved.append(
                    UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
                )
                continue
        elif kind == "SQL_LITERAL":
            unresolved.append(
                UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
            )
            continue
        elif kind == "CONFIG_KEY_REF" and value and config_index:
            if value in config_index:
                target_type, target_id = "ConfigSection", value
            else:
                unresolved.append(
                    UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
                )
                continue

        # 호출/인스턴스 → Class 결정 (실패 시 skip, deepagent 대상 아님)
        elif kind == "CALL_REF":
            receiver = c.get("receiver")
            receiver_type = (sym_idx.get(function_id) or {}).get(receiver) if receiver else None
            if receiver_type:
                info = module_idx.get(receiver_type)
                if isinstance(info, dict):
                    target_type, target_id = "Module", info.get("fqn") or receiver_type
                elif info:
                    target_type, target_id = "Module", str(info)
            if not target_type:
                continue  # 클래스 매핑 실패 → skip (deepagent 대상 아님)
        elif kind == "NEW_INSTANCE_REF":
            module_name = c.get("moduleName")
            if module_name:
                info = module_idx.get(module_name)
                if isinstance(info, dict):
                    target_type, target_id = "Module", info.get("fqn", module_name)
                elif module_name in module_idx:
                    target_type, target_id = "Module", module_name
            if not target_type:
                continue  # 클래스 매핑 실패 → skip (deepagent 대상 아님)

        else:
            continue  # 알 수 없는 kind → skip

        if target_type and target_id:
            edges.append(
                ReferToEdge(
                    function_id=function_id,
                    target_type=target_type,
                    target_id=target_id,
                    evidence={"file": code_ref, "candidate_kind": kind},
                    confidence=1.0,
                    variable_id=None,
                )
            )

    return edges, unresolved
