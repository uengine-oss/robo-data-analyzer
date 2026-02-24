"""UML 관계 처리 모듈

ASSOCIATION, COMPOSITION, AGGREGATION, DEPENDENCY, EXTENDS, IMPLEMENTS, USES
관계의 Neo4j 쿼리 생성 및 Phase 1.5 ASSOCIATION 오케스트레이션을 한 곳에서 관리합니다.

호출자: _ast_core.py, framework_pipeline.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from util.stream_event import emit_data, emit_message, emit_phase_event
from util.text_utils import log_process

# 타입 매핑용 상수
FIELD_TYPES = frozenset(["FIELD"])
METHOD_TYPES = frozenset(["METHOD", "CONSTRUCTOR"])


# =============================================================================
# 타입 매핑 헬퍼 (CALLS/USES 타겟 클래스 해석용)
# =============================================================================

def build_field_type_map(nodes: List[Any]) -> Dict[str, str]:
    """FIELD 노드에서 field_name → fieldType 매핑 구축"""
    return {
        node.name: node.field_type
        for node in nodes
        if node.node_type in FIELD_TYPES and node.field_type and node.name
    }


def build_param_type_map(node: Any) -> Dict[str, str]:
    """parameters 문자열에서 paramName → paramType 매핑 생성"""
    param_map: Dict[str, str] = {}
    parameters = getattr(node, "parameters", None)
    if not parameters or not isinstance(parameters, str):
        return param_map

    raw = parameters.strip()
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


def build_local_var_type_map(method_node: Any) -> Dict[str, str]:
    """메서드 내부 VARIABLE 노드에서 varName → varType 매핑 생성"""
    local_map: Dict[str, str] = {}
    conflicts: set = set()

    def visit(n: Any) -> None:
        if n is not method_node and getattr(n, "node_type", None) in METHOD_TYPES:
            return

        for child in getattr(n, "children", []):
            if getattr(child, "node_type", None) == "VARIABLE":
                field_type = getattr(child, "field_type", None)
                name = getattr(child, "name", None)
                if field_type and name:
                    var_name = name
                    var_type = field_type
                    if var_name in local_map and local_map[var_name] != var_type:
                        conflicts.add(var_name)
                    else:
                        local_map[var_name] = var_type

            if getattr(child, "children", []):
                visit(child)

    visit(method_node)

    for name in conflicts:
        local_map.pop(name, None)

    return local_map


# =============================================================================
# EXTENDS / IMPLEMENTS (AST 기반)
# =============================================================================

def build_inheritance_queries(nodes: List[Any]) -> List[Dict[str, Any]]:
    """AST 노드에서 EXTENDS, IMPLEMENTS 관계 쿼리 생성"""
    queries: List[Dict[str, Any]] = []

    for node in nodes:
        if not node.unit_name:
            continue

        if node.extends_type:
            target_class = node.extends_type.strip()
            if target_class:
                queries.append({
                    "query": (
                        "MERGE (__cy_ext__:CLASS:EXTERNAL {class_name: $target_class})\n"
                        "SET __cy_ext__.name = $target_class\n"
                        "RETURN __cy_ext__"
                    ),
                    "params": {"target_class": target_class}
                })
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                        f"MATCH (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                        f"MERGE (__cy_src__)-[__cy_r__:EXTENDS]->(__cy_ext__)\n"
                        f"RETURN __cy_r__"
                    ),
                    "params": {
                        "package_name": node.package_name,
                        "class_name": node.unit_name,
                        "target_class": target_class,
                    }
                })
                log_process("ANALYZE", "INHERITANCE",
                           f"   🔗 상속: {node.unit_name} extends {target_class} (EXTERNAL)")

        if node.implements_types:
            interfaces = [i.strip() for i in node.implements_types.split(',')]
            for iface in interfaces:
                if not iface:
                    continue
                queries.append({
                    "query": (
                        f"MATCH (__cy_src__:{node.node_type} {{package_name: $package_name, class_name: $class_name}})\n"
                        f"OPTIONAL MATCH (__cy_iface__:INTERFACE) WHERE toLower(__cy_iface__.class_name) = toLower($target_iface)\n"
                        f"WITH __cy_src__, __cy_iface__\n"
                        f"CALL {{\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__, __cy_iface__ WHERE __cy_iface__ IS NOT NULL\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_iface__)\n"
                        f"  RETURN 1 AS done\n"
                        f"  UNION ALL\n"
                        f"  WITH __cy_src__, __cy_iface__\n"
                        f"  WITH __cy_src__ WHERE __cy_iface__ IS NULL\n"
                        f"  MERGE (__cy_ext__:INTERFACE:EXTERNAL {{class_name: $target_iface}})\n"
                        f"  SET __cy_ext__.name = $target_iface\n"
                        f"  MERGE (__cy_src__)-[:IMPLEMENTS]->(__cy_ext__)\n"
                        f"  RETURN 1 AS done\n"
                        f"}}\n"
                        f"RETURN done"
                    ),
                    "params": {
                        "package_name": node.package_name,
                        "class_name": node.unit_name,
                        "target_iface": iface,
                    }
                })
                log_process("ANALYZE", "INHERITANCE",
                           f"   🔗 구현: {node.unit_name} implements {iface}")

    return queries


def build_external_resolution_queries() -> List[Dict[str, Any]]:
    """EXTERNAL → INTERNAL 전환 및 EXTERNAL 노드 정리 쿼리"""
    return [
        {
            "query": (
                "MATCH (src)-[r:EXTENDS]->(ext:EXTERNAL)\n"
                "MATCH (real:CLASS:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:EXTENDS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS extends_resolved"
            ),
            "params": {}
        },
        {
            "query": (
                "MATCH (src)-[r:IMPLEMENTS]->(ext:EXTERNAL)\n"
                "MATCH (real:INTERFACE:INTERNAL) WHERE toLower(real.class_name) = toLower(ext.class_name)\n"
                "MERGE (src)-[:IMPLEMENTS]->(real)\n"
                "DELETE r\n"
                "RETURN count(*) AS implements_resolved"
            ),
            "params": {}
        },
        {
            "query": (
                "MATCH (ext:EXTERNAL)\n"
                "WHERE EXISTS { MATCH (real:INTERNAL) WHERE (real:CLASS OR real:INTERFACE)\n"
                "  AND toLower(real.class_name) = toLower(ext.class_name) }\n"
                "DETACH DELETE ext\n"
                "RETURN count(*) AS external_cleaned"
            ),
            "params": {}
        },
    ]


# =============================================================================
# ASSOCIATION (필드 기반, Phase 1.5)
# =============================================================================

def build_association_queries(
    node_type: str,
    package_name: str,
    class_name: str,
    field_relations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """필드 기반 ASSOCIATION 관계 쿼리 생성"""
    queries: List[Dict[str, Any]] = []

    for rel in field_relations:
        target_class = rel.get("targetClass")
        if not target_class:
            continue
        multiplicity = rel.get("multiplicity", "1")
        field_name = rel.get("field", "")

        queries.append({
            "query": (
                f"MATCH (__cy_src__:{node_type}:INTERNAL {{package_name: $package_name, class_name: $class_name}})\n"
                f"OPTIONAL MATCH (__cy_int__:INTERNAL) WHERE (__cy_int__:CLASS OR __cy_int__:INTERFACE)\n"
                f"  AND toLower(__cy_int__.class_name) = toLower($target_class)\n"
                f"WITH __cy_src__, __cy_int__\n"
                f"CALL {{\n"
                f"  WITH __cy_src__, __cy_int__\n"
                f"  WITH __cy_src__, __cy_int__ WHERE __cy_int__ IS NOT NULL\n"
                f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_int__)\n"
                f"  RETURN 1 AS done\n"
                f"  UNION ALL\n"
                f"  WITH __cy_src__, __cy_int__\n"
                f"  WITH __cy_src__ WHERE __cy_int__ IS NULL\n"
                f"  MERGE (__cy_ext__:CLASS:EXTERNAL {{class_name: $target_class}})\n"
                f"  SET __cy_ext__.name = $target_class\n"
                f"  MERGE (__cy_src__)-[:ASSOCIATION {{field: $field_name, multiplicity: $multiplicity}}]->(__cy_ext__)\n"
                f"  RETURN 1 AS done\n"
                f"}}\n"
                f"RETURN done"
            ),
            "params": {
                "package_name": package_name,
                "class_name": class_name,
                "target_class": target_class,
                "field_name": field_name,
                "multiplicity": multiplicity,
            }
        })
        log_process("ANALYZE", "ASSOCIATION",
                   f"   🔗 ASSOCIATION: {class_name}.{field_name} → {target_class} ({multiplicity})")

    return queries


# =============================================================================
# DEPENDENCY / COMPOSITION / AGGREGATION / USES (Phase 2, analysis.yaml dependencies)
# =============================================================================

def build_dependency_queries(
    src_class: str,
    dep: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """analysis.yaml dependencies → DEPENDENCY/COMPOSITION/AGGREGATION + USES 쿼리"""
    queries: List[Dict[str, Any]] = []
    target_class = dep.get("targetClass")
    if not target_class:
        return queries

    source_member = dep.get("sourceMember", "")
    usage = dep.get("usage", "")
    rel_type = dep.get("relationType", "DEPENDENCY")

    if rel_type in ("COMPOSITION", "AGGREGATION"):
        queries.append({
            "query": (
                "MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                "  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                "MATCH (__cy_tgt__) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                "  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                "OPTIONAL MATCH (__cy_src__)-[__old__:ASSOCIATION]->(__cy_tgt__)\n"
                "WITH __cy_src__, __cy_tgt__, __old__, __old__.field AS f, __old__.multiplicity AS m\n"
                "DELETE __old__\n"
                "MERGE (__cy_src__)-[__cy_r__:" + rel_type + " {field: COALESCE(f, ''), multiplicity: COALESCE(m, '1'), source_member: $source_member}]->(__cy_tgt__)\n"
                "RETURN __cy_r__"
            ),
            "params": {
                "src_class": src_class,
                "target_class": target_class,
                "source_member": source_member,
            }
        })
        log_process("ANALYZE", "RELATION_UPGRADE",
                   f"   🔗 ASSOCIATION→{rel_type}: {src_class} → {target_class}")
    else:
        queries.append({
            "query": (
                "MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
                "  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
                "MATCH (__cy_tgt__:INTERNAL) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
                "  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
                "OPTIONAL MATCH (__cy_src__)-[__rel__:ASSOCIATION|COMPOSITION|AGGREGATION]->(__cy_tgt__)\n"
                "WITH __cy_src__, __cy_tgt__, __rel__\n"
                "WHERE __rel__ IS NULL\n"
                "MERGE (__cy_src__)-[__cy_r__:DEPENDENCY {source_member: $source_member, usage: $usage}]->(__cy_tgt__)\n"
                "RETURN __cy_r__"
            ),
            "params": {
                "src_class": src_class,
                "target_class": target_class,
                "source_member": source_member,
                "usage": usage,
            }
        })

    queries.append(build_uses_class_to_class_query(src_class, target_class))
    return queries


def build_uses_class_to_class_query(src_class: str, target_class: str) -> Dict[str, Any]:
    """클래스 간 USES 관계 쿼리"""
    return {
        "query": (
            "MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
            "  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
            "MATCH (__cy_tgt__:INTERNAL) WHERE (__cy_tgt__:CLASS OR __cy_tgt__:INTERFACE)\n"
            "  AND toLower(__cy_tgt__.class_name) = toLower($target_class)\n"
            "  AND __cy_src__ <> __cy_tgt__\n"
            "MERGE (__cy_src__)-[:USES]->(__cy_tgt__)\n"
            "RETURN __cy_src__, __cy_tgt__"
        ),
        "params": {
            "src_class": src_class,
            "target_class": target_class,
        }
    }


def build_uses_class_to_method_query(
    src_class: str,
    target_class: str,
    call_method: str,
) -> Dict[str, Any]:
    """클래스 → 타겟 메서드 USES 관계 쿼리"""
    return {
        "query": (
            "MATCH (__cy_src__:INTERNAL) WHERE (__cy_src__:CLASS OR __cy_src__:INTERFACE)\n"
            "  AND toLower(__cy_src__.class_name) = toLower($src_class)\n"
            "MATCH (__cy_tgt_cls__:INTERNAL) WHERE (__cy_tgt_cls__:CLASS OR __cy_tgt_cls__:INTERFACE)\n"
            "  AND toLower(__cy_tgt_cls__.class_name) = toLower($target_class)\n"
            "MATCH (__cy_tgt_cls__)-[:HAS_METHOD]->(__cy_tgt_m__:METHOD)\n"
            "  WHERE toLower(__cy_tgt_m__.name) = toLower($call_method)\n"
            "MERGE (__cy_src__)-[:USES]->(__cy_tgt_m__)\n"
            "RETURN __cy_src__, __cy_tgt_m__"
        ),
        "params": {
            "src_class": src_class,
            "target_class": target_class,
            "call_method": call_method,
        }
    }


# =============================================================================
# Dependencies + Calls 통합 처리 (analysis.yaml 결과 → 쿼리)
# =============================================================================

def build_calls_query(
    node_type: str,
    method_line: int,
    full_directory: str,
    file_name: str,
    target_class: str,
    method_name: str,
    call_expr: str,
) -> Dict[str, Any]:
    """METHOD-to-METHOD CALLS 관계 쿼리"""
    return {
        "query": (
            f"MATCH (__cy_m__:{node_type} {{start_line: $method_line, directory: $directory, file_name: $file_name}})\n"
            "MATCH (__cy_tgt_cls__:INTERNAL) WHERE (__cy_tgt_cls__:CLASS OR __cy_tgt_cls__:INTERFACE)\n"
            "  AND toLower(__cy_tgt_cls__.class_name) = toLower($target_class)\n"
            "MATCH (__cy_tgt_cls__)-[:HAS_METHOD]->(__cy_tgt_m__:METHOD)\n"
            "  WHERE toLower(__cy_tgt_m__.name) = toLower($call_method)\n"
            "MERGE (__cy_m__)-[__cy_r__:CALLS {method: $call_method, code: $call_code}]->(__cy_tgt_m__)\n"
            "RETURN __cy_r__"
        ),
        "params": {
            "method_line": method_line,
            "directory": full_directory,
            "file_name": file_name,
            "target_class": target_class,
            "call_method": method_name,
            "call_code": call_expr[:200],
        }
    }


def build_dependency_and_call_queries(
    node: Any,
    analysis: Dict[str, Any],
    param_type_map: Dict[str, str],
    local_type_map: Dict[str, str],
    field_map: Dict[str, str],
    full_directory: str,
    file_name: str,
) -> List[Dict[str, Any]]:
    """analysis.yaml의 dependencies + calls → DEPENDENCY/CALLS/USES 쿼리 목록"""
    queries: List[Dict[str, Any]] = []
    node_type = getattr(node, "node_type", "")

    for dep in analysis.get("dependencies", []):
        if not dep.get("targetClass") or not getattr(node, "unit_name", None):
            continue
        queries.extend(build_dependency_queries(getattr(node, "unit_name", ""), dep))

    for call_expr in analysis.get("calls", []):
        if not isinstance(call_expr, str) or "." not in call_expr:
            continue

        parts = call_expr.split(".", 1)
        object_name = parts[0].strip()
        method_name = parts[1].strip() if len(parts) > 1 else ""

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
            continue

        queries.append(build_calls_query(
            node_type,
            node.start_line,
            full_directory,
            file_name,
            target_class,
            method_name,
            call_expr,
        ))

        if getattr(node, "unit_name", None):
            queries.append(build_uses_class_to_class_query(node.unit_name, target_class))
            queries.append(build_uses_class_to_method_query(node.unit_name, target_class, method_name))

    return queries


# =============================================================================
# Phase 1.5: ASSOCIATION 오케스트레이션
# =============================================================================

async def run_phase1_5_association(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1.5: UML ASSOCIATION 관계 생성 (클래스별 병렬 처리)"""
    from analyzer.strategy.framework._ast_core import CLASS_TYPES, FIELD_TYPES, analyze_association

    success_contexts = [ctx for ctx in contexts if ctx.status == FileStatus.PH1_OK and ctx.processor]

    if not success_contexts:
        yield emit_message("   ℹ️ Phase 1.5: 처리할 파일 없음")
        return

    completed = 0
    total = len(success_contexts)
    results_queue: asyncio.Queue = asyncio.Queue()

    async def process_class_associations(ctx: FileAnalysisContext):
        async with analyzer._file_semaphore:
            try:
                if not ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {ctx.file_name}")

                all_queries = []
                all_nodes = {}
                all_relationships = {}

                for node in ctx.processor._nodes:
                    if node.node_type not in CLASS_TYPES:
                        continue

                    fields = [
                        {
                            "name": child.name,
                            "field_type": child.field_type,
                            "modifiers": child.modifiers,
                            "annotations": child.annotations,
                            "comment": child.comment,
                            "code": child.code,
                        }
                        for child in node.children
                        if child.node_type in FIELD_TYPES and child.field_type
                    ]

                    if not fields:
                        continue

                    class_meta_parts = []
                    if node.comment:
                        class_meta_parts.append(node.comment)
                    if node.annotations:
                        class_meta_parts.append(node.annotations)
                    class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""

                    result = await asyncio.to_thread(
                        analyze_association,
                        node.unit_name,
                        class_meta,
                        fields,
                        orchestrator.api_key,
                        orchestrator.locale,
                    )
                    validated = ctx.processor.validate_dict_result(result, "association")
                    field_relations = validated.get("fieldRelations", [])
                    all_queries.extend(build_association_queries(
                        node.node_type,
                        node.package_name,
                        node.unit_name,
                        field_relations,
                    ))

                if all_queries:
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(all_queries):
                            for n in batch_result.get("Nodes", []):
                                all_nodes[n["Node ID"]] = n
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel

                    log_process("ANALYZE", "PHASE_1.5", f"✅ {ctx.file_name}: ASSOCIATION {len(all_queries)}개 생성")

                graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                await results_queue.put({
                    "type": "success",
                    "file": ctx.file_name,
                    "query_count": len(all_queries),
                    "graph": graph,
                })

            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 1.5 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise

    tasks = [asyncio.create_task(process_class_associations(ctx)) for ctx in success_contexts]

    yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "started", 0, {"canPause": True, "files": total})

    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")

        completed += 1

        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}: {result.get('query_count', 0)}개 ASSOCIATION")
            graph = result.get("graph")
            if graph:
                yield emit_data(graph=graph)

            progress = int(completed / total * 100)
            yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "progress", progress, {"completed": completed, "total": total})

    await asyncio.gather(*tasks, return_exceptions=True)

    yield emit_phase_event(1.5, "UML ASSOCIATION 생성", "completed", 100, {})
