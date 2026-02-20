"""Phase 5 (nontarget): Ontology Construction - Build Neo4j graph.

노드 타입/속성을 Framework 스키마에 맞춤:
- JavaClass → CLASS
- Interface → INTERFACE
- Method → METHOD
- Field → FIELD
- Package → PACKAGE (Framework와 통일)

Framework 필수 속성 추가:
- start_line, end_line, directory, file_name

패키지 노드 생성 및 통합:
- 디렉토리 경로를 패키지명으로 변환
- Framework의 _build_package_hierarchy_queries 로직 참조
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.nontarget.data_models import (
    Chunk,
    EdgeType,
    FileCategory,
    FileInfo,
    GraphEdge,
    GraphNode,
    ProjectStructure,
    Relationship,
)
from analyzer.strategy.framework.nontarget.phase4_relationships import _map_edge_type_to_framework


def _map_node_label_to_framework(label: str) -> str:
    """Map deep-agents node label to Framework schema."""
    mapping = {
        "JavaClass": "CLASS",
        "Interface": "INTERFACE",
        "Method": "METHOD",
        "Field": "FIELD",
        "Package": "PACKAGE",  # Framework와 통일
        "SqlMap": "SqlMap",
        "SqlStatement": "SqlStatement",
        "SpringBean": "SpringBean",
        "ConfigFile": "ConfigFile",
    }
    return mapping.get(label, label)


def _extract_directory_from_path(relative_path: str) -> str:
    """Extract directory from relative path."""
    parts = relative_path.replace("\\", "/").split("/")
    if len(parts) > 1:
        return "/".join(parts[:-1])
    return ""


def _path_to_package_name(relative_path: str) -> str:
    """Convert directory path to package name (e.g., com/example/service → com.example.service)."""
    directory = _extract_directory_from_path(relative_path)
    if directory:
        return directory.replace("/", ".")
    return ""


async def run_phase5_ontology(
    project: ProjectStructure,
    chunks: list[Chunk],
    relationships: list[Relationship],
    client: Neo4jClient,
    class_index: dict[str, dict[str, Any]] | None = None,
    bean_index: dict[str, dict[str, Any]] | None = None,
    sql_index: dict[str, dict[str, Any]] | None = None,
) -> AsyncGenerator[bytes, None]:
    """Phase 5 (nontarget): 온톨로지 구축 - Neo4j 그래프 생성
    
    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        chunks: Phase 3에서 생성된 청크
        relationships: Phase 4에서 발견된 관계
        client: Neo4j 클라이언트
        class_index: 클래스 인덱스 (Phase 4에서 생성)
        bean_index: 빈 인덱스 (Phase 4에서 생성)
        sql_index: SQL 인덱스 (Phase 4에서 생성)
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 5 시작 이벤트
    yield emit_phase_event(5, "비타겟 온톨로지 구축", "started", 0, {"canPause": True})
    
    try:
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        
        # 5-A: Create nodes
        log_process("ANALYZE", "PHASE_5", "노드 생성 중...")
        
        # Package nodes (디렉토리 기반)
        package_nodes, package_edges = _create_package_nodes_from_files(project.files, chunks)
        nodes.extend(package_nodes)
        edges.extend(package_edges)
        log_process("ANALYZE", "PHASE_5", f"패키지 노드 {len(package_nodes)}개 생성")
        
        # Class/Interface nodes (nontarget에서 클래스는 거의 없지만, 혹시 모를 경우 대비)
        class_nodes, interface_nodes = _create_class_and_interface_nodes(chunks)
        nodes.extend(class_nodes)
        nodes.extend(interface_nodes)
        log_process("ANALYZE", "PHASE_5", f"클래스 노드 {len(class_nodes)}개, 인터페이스 노드 {len(interface_nodes)}개 생성")
        
        # Method/Field nodes (nontarget에서도 가능)
        method_nodes = _create_method_nodes(chunks)
        field_nodes = _create_field_nodes(chunks)
        nodes.extend(method_nodes)
        nodes.extend(field_nodes)
        log_process("ANALYZE", "PHASE_5", f"메서드 노드 {len(method_nodes)}개, 필드 노드 {len(field_nodes)}개 생성")
        
        # SQL Map nodes
        sqlmap_nodes = _create_sqlmap_nodes(chunks)
        nodes.extend(sqlmap_nodes)
        log_process("ANALYZE", "PHASE_5", f"SQL Map 노드 {len(sqlmap_nodes)}개 생성")
        
        # SQL Statement nodes
        sql_statement_nodes = _create_sql_statement_nodes(chunks)
        nodes.extend(sql_statement_nodes)
        log_process("ANALYZE", "PHASE_5", f"SQL Statement 노드 {len(sql_statement_nodes)}개 생성")
        
        # Spring Bean nodes
        spring_bean_nodes = _create_spring_bean_nodes(chunks)
        nodes.extend(spring_bean_nodes)
        log_process("ANALYZE", "PHASE_5", f"Spring Bean 노드 {len(spring_bean_nodes)}개 생성")
        
        # Config nodes
        config_nodes = _create_config_nodes(project, chunks)
        nodes.extend(config_nodes)
        log_process("ANALYZE", "PHASE_5", f"Config 노드 {len(config_nodes)}개 생성")
        
        # 5-B: Create edges
        log_process("ANALYZE", "PHASE_5", "관계 생성 중...")
        
        # Containment edges
        containment_edges = _create_containment_edges(chunks)
        edges.extend(containment_edges)
        
        # Phase 4 relationships (Framework 스키마에 맞춰 변환)
        for rel in relationships:
            edge_type_str = _map_edge_type_to_framework(rel.edge_type)
            edges.append(GraphEdge(
                source_id=rel.source_id,
                target_id=rel.target_id,
                edge_type=edge_type_str,  # Framework 스키마에 맞춘 문자열
                properties={
                    **rel.properties,
                    "confidence": rel.confidence,
                    "discovery_method": rel.discovery_method,
                },
            ))
        
        log_process("ANALYZE", "PHASE_5", f"관계 {len(edges)}개 생성")
        
        # 5-C: Load into Neo4j (즉시 스트리밍)
        log_process("ANALYZE", "PHASE_5", "Neo4j에 로드 중...")
        
        # 노드를 배치로 저장 (30개씩)
        batch_size = 30
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            queries = _nodes_to_cypher_queries(batch)
            async for batch_result in client.run_graph_query(queries):
                yield emit_data(
                    graph={
                        "Nodes": batch_result.get("Nodes", []),
                        "Relationships": batch_result.get("Relationships", []),
                    }
                )
            yield emit_message(f"   ✓ 노드 배치 {i//batch_size + 1}/{(len(nodes) + batch_size - 1)//batch_size} 저장 완료")
        
        # 관계를 배치로 저장 (30개씩)
        for i in range(0, len(edges), batch_size):
            batch = edges[i:i + batch_size]
            queries = _edges_to_cypher_queries(batch)
            async for batch_result in client.run_graph_query(queries):
                yield emit_data(
                    graph={
                        "Nodes": batch_result.get("Nodes", []),
                        "Relationships": batch_result.get("Relationships", []),
                    }
                )
            yield emit_message(f"   ✓ 관계 배치 {i//batch_size + 1}/{(len(edges) + batch_size - 1)//batch_size} 저장 완료")
        
        log_process("ANALYZE", "PHASE_5", f"요약: {len(nodes)}개 노드, {len(edges)}개 관계")
        
        # Phase 5 완료 이벤트
        yield emit_phase_event(5, "비타겟 온톨로지 구축", "completed", 100, {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        })
        
        # Phase 결과 반환
        yield emit_phase_result(5, {
            "nodes": nodes,
            "edges": edges,
        })
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 5 실패: {e}", exc_info=True)
        yield emit_phase_event(5, "비타겟 온톨로지 구축", "failed", 0, {"error": str(e)})
        raise


def _create_package_nodes_from_files(
    files: list[FileInfo],
    chunks: list[Chunk],
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Create package nodes from file paths and chunks.
    
    Framework의 _build_package_hierarchy_queries 로직을 참조하여 동일한 방식으로 구현.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    
    # Collect all package names from files and chunks
    packages: set[str] = set()
    
    # From file paths
    for file_info in files:
        pkg = _path_to_package_name(file_info.relative_path)
        if pkg:
            packages.add(pkg)
    
    # From chunks (if they have package property)
    for chunk in chunks:
        if chunk.metadata.chunk_type == "class":
            pkg = chunk.metadata.properties.get("package", "")
            if pkg:
                packages.add(pkg)
        # Also check file path
        pkg = _path_to_package_name(chunk.metadata.source_file)
        if pkg:
            packages.add(pkg)
    
    # Create package hierarchy (Framework 방식)
    for package_name in packages:
        parts = package_name.split(".")
        for i in range(len(parts)):
            segment = parts[i]
            fqn = ".".join(parts[:i + 1])
            parent_fqn = ".".join(parts[:i]) if i > 0 else None
            
            # Package node
            nodes.append(GraphNode(
                node_id=f"pkg:{fqn}",
                label="PACKAGE",  # Framework와 통일
                properties={
                    "fqn": fqn,
                    "name": segment,
                },
            ))
            
            # Package hierarchy edge
            if parent_fqn:
                edges.append(GraphEdge(
                    source_id=f"pkg:{parent_fqn}",
                    target_id=f"pkg:{fqn}",
                    edge_type="CONTAINS_PACKAGE",
                    properties={},
                ))
    
    return nodes, edges


def _create_class_and_interface_nodes(chunks: list[Chunk]) -> tuple[list[GraphNode], list[GraphNode]]:
    """Create CLASS and INTERFACE nodes (Framework 스키마에 맞춤)."""
    class_nodes: list[GraphNode] = []
    interface_nodes: list[GraphNode] = []
    
    for chunk in chunks:
        if chunk.metadata.chunk_type == "class":
            props = chunk.metadata.properties
            type_kind = props.get("type_kind", "ClassDeclaration")
            name = props.get("name", "")
            fqn = props.get("fqn", "")
            package_name = props.get("package", "")
            
            # Framework 필수 속성 추가
            source_file = chunk.metadata.source_file
            directory = _extract_directory_from_path(source_file)
            file_name = Path(source_file).name
            
            node_props = {
                "class_name": name,  # name → class_name
                "fqn": fqn,
                "package_name": package_name,
                "modifiers": ",".join(props.get("access_modifier", [])) if isinstance(props.get("access_modifier"), list) else "",
                "annotations": ",".join(props.get("annotations", [])) if isinstance(props.get("annotations"), list) else "",
                # Framework 필수 속성
                "start_line": chunk.metadata.start_line,
                "end_line": chunk.metadata.end_line,
                "directory": directory,
                "file_name": file_name,
            }
            
            is_interface = "Interface" in type_kind or (name.startswith("I") and len(name) > 1 and name[1].isupper())
            
            if is_interface:
                interface_nodes.append(GraphNode(
                    node_id=chunk.chunk_id,
                    label="INTERFACE",  # Framework와 통일
                    properties=node_props,
                ))
            else:
                class_nodes.append(GraphNode(
                    node_id=chunk.chunk_id,
                    label="CLASS",  # Framework와 통일
                    properties=node_props,
                ))
    
    return class_nodes, interface_nodes


def _create_method_nodes(chunks: list[Chunk]) -> list[GraphNode]:
    """Create METHOD nodes (Framework 스키마에 맞춤)."""
    nodes: list[GraphNode] = []
    for chunk in chunks:
        for sub in chunk.sub_chunks:
            if sub.metadata.chunk_type == "method":
                props = sub.metadata.properties
                source_file = sub.metadata.source_file
                directory = _extract_directory_from_path(source_file)
                file_name = Path(source_file).name
                
                nodes.append(GraphNode(
                    node_id=sub.chunk_id,
                    label="METHOD",  # Framework와 통일
                    properties={
                        "name": props.get("name", ""),
                        "signature": props.get("fqn", ""),  # fqn → signature
                        "return_type": props.get("return_type", ""),
                        "parameters": str(props.get("parameters", [])),
                        "modifiers": ",".join(props.get("access_modifier", [])) if isinstance(props.get("access_modifier"), list) else "",
                        # Framework 필수 속성
                        "start_line": sub.metadata.start_line,
                        "end_line": sub.metadata.end_line,
                        "directory": directory,
                        "file_name": file_name,
                    },
                ))
    return nodes


def _create_field_nodes(chunks: list[Chunk]) -> list[GraphNode]:
    """Create FIELD nodes (Framework 스키마에 맞춤)."""
    nodes: list[GraphNode] = []
    for chunk in chunks:
        for sub in chunk.sub_chunks:
            if sub.metadata.chunk_type == "field":
                props = sub.metadata.properties
                source_file = sub.metadata.source_file
                directory = _extract_directory_from_path(source_file)
                file_name = Path(source_file).name
                
                nodes.append(GraphNode(
                    node_id=sub.chunk_id,
                    label="FIELD",  # Framework와 통일
                    properties={
                        "name": props.get("name", ""),
                        "field_type": props.get("type", ""),  # type → field_type
                        "modifiers": ",".join(props.get("access_modifier", [])) if isinstance(props.get("access_modifier"), list) else "",
                        # Framework 필수 속성
                        "start_line": sub.metadata.start_line,
                        "end_line": sub.metadata.end_line,
                        "directory": directory,
                        "file_name": file_name,
                    },
                ))
    return nodes


def _create_sqlmap_nodes(chunks: list[Chunk]) -> list[GraphNode]:
    """Create SqlMap nodes."""
    sqlmap_files: dict[str, str] = {}
    for chunk in chunks:
        if chunk.metadata.chunk_type == "sql_statement":
            f = chunk.metadata.source_file
            ns = chunk.metadata.properties.get("namespace", "")
            if f not in sqlmap_files:
                sqlmap_files[f] = ns
    
    nodes: list[GraphNode] = []
    for fp, ns in sqlmap_files.items():
        directory = _extract_directory_from_path(fp)
        file_name = Path(fp).name
        nodes.append(GraphNode(
            node_id=f"sqlmap:{fp}",
            label="SqlMap",
            properties={
                "namespace": ns,
                "file_name": file_name,
                "directory": directory,
                "start_line": 0,
                "end_line": 0,
            },
        ))
    return nodes


def _create_sql_statement_nodes(chunks: list[Chunk]) -> list[GraphNode]:
    """Create SqlStatement nodes."""
    nodes: list[GraphNode] = []
    for chunk in chunks:
        if chunk.metadata.chunk_type == "sql_statement":
            props = chunk.metadata.properties
            source_file = chunk.metadata.source_file
            directory = _extract_directory_from_path(source_file)
            file_name = Path(source_file).name
            
            nodes.append(GraphNode(
                node_id=chunk.chunk_id,
                label="SqlStatement",
                properties={
                    "sql_id": props.get("id", ""),  # id → sql_id
                    "sql_type": props.get("type", ""),  # type → sql_type
                    "sql_text": chunk.content,  # sqlText → sql_text
                    "namespace": props.get("namespace", ""),
                    # Framework 필수 속성
                    "start_line": chunk.metadata.start_line,
                    "end_line": chunk.metadata.end_line,
                    "directory": directory,
                    "file_name": file_name,
                },
            ))
    return nodes


def _create_spring_bean_nodes(chunks: list[Chunk]) -> list[GraphNode]:
    """Create SpringBean nodes."""
    nodes: list[GraphNode] = []
    for chunk in chunks:
        if chunk.metadata.chunk_type == "spring_bean":
            props = chunk.metadata.properties
            source_file = chunk.metadata.source_file
            directory = _extract_directory_from_path(source_file)
            file_name = Path(source_file).name
            
            nodes.append(GraphNode(
                node_id=chunk.chunk_id,
                label="SpringBean",
                properties={
                    "id": props.get("id", ""),
                    "name": props.get("id", ""),
                    "class": props.get("class", ""),
                    "scope": props.get("scope", ""),
                    "lazyInit": props.get("lazy_init", "false"),
                    # Framework 필수 속성
                    "start_line": chunk.metadata.start_line,
                    "end_line": chunk.metadata.end_line,
                    "directory": directory,
                    "file_name": file_name,
                },
            ))
    return nodes


def _create_config_nodes(project: ProjectStructure, chunks: list[Chunk]) -> list[GraphNode]:
    """Create ConfigFile nodes."""
    nodes: list[GraphNode] = []
    config_files = [f for f in project.files if f.category in (FileCategory.YAML, FileCategory.PROPERTIES, FileCategory.JSON)]
    
    for file_info in config_files:
        directory = _extract_directory_from_path(file_info.relative_path)
        file_name = Path(file_info.relative_path).name
        
        nodes.append(GraphNode(
            node_id=f"config:{file_info.relative_path}",
            label="ConfigFile",
            properties={
                "file_name": file_name,
                "directory": directory,
                "category": file_info.category.value,
                "start_line": 0,
                "end_line": 0,
            },
        ))
    
    return nodes


def _create_containment_edges(chunks: list[Chunk]) -> list[GraphEdge]:
    """Create containment edges (CONTAINS, HAS_METHOD, HAS_FIELD)."""
    edges: list[GraphEdge] = []
    
    for chunk in chunks:
        # Class → Method/Field
        if chunk.metadata.chunk_type == "class":
            for sub in chunk.sub_chunks:
                if sub.metadata.chunk_type == "method":
                    edges.append(GraphEdge(
                        source_id=chunk.chunk_id,
                        target_id=sub.chunk_id,
                        edge_type="HAS_METHOD",
                        properties={},
                    ))
                elif sub.metadata.chunk_type == "field":
                    edges.append(GraphEdge(
                        source_id=chunk.chunk_id,
                        target_id=sub.chunk_id,
                        edge_type="HAS_FIELD",
                        properties={},
                    ))
        
        # Package → Class (BELONGS_TO_PACKAGE)
        if chunk.metadata.chunk_type == "class":
            props = chunk.metadata.properties
            package_name = props.get("package", "")
            if not package_name:
                # Try to extract from file path
                package_name = _path_to_package_name(chunk.metadata.source_file)
            
            if package_name:
                edges.append(GraphEdge(
                    source_id=chunk.chunk_id,
                    target_id=f"pkg:{package_name}",
                    edge_type="BELONGS_TO_PACKAGE",
                    properties={},
                ))
    
    return edges


def _nodes_to_cypher_queries(nodes: list[GraphNode]) -> list[dict[str, Any]]:
    """Convert GraphNode list to Cypher queries."""
    queries: list[dict[str, Any]] = []
    
    for node in nodes:
        label = node.label  # 이미 Framework 스키마에 맞춘 문자열
        props = node.properties.copy()
        
        # node_id를 properties에 추가 (MERGE 조건용)
        node_id_prop = props.get("fqn") or props.get("id") or props.get("sql_id") or node.node_id
        
        query = f"MERGE (__cy_n__:{label} {{"
        query_parts = []
        params: dict[str, Any] = {}
        
        # node_id를 고유 식별자로 사용
        if "fqn" in props:
            query_parts.append("fqn: $fqn")
            params["fqn"] = props["fqn"]
        elif "id" in props:
            query_parts.append("id: $id")
            params["id"] = props["id"]
        elif "sql_id" in props:
            query_parts.append("sql_id: $sql_id")
            params["sql_id"] = props["sql_id"]
        else:
            # Fallback: node_id 사용
            query_parts.append("node_id: $node_id")
            params["node_id"] = node.node_id
        
        query += ", ".join(query_parts)
        query += "})\n"
        query += "SET __cy_n__ += $props\n"
        query += "RETURN __cy_n__"
        
        params["props"] = props
        
        queries.append({
            "query": query,
            "params": params,
        })
    
    return queries


def _edges_to_cypher_queries(edges: list[GraphEdge]) -> list[dict[str, Any]]:
    """Convert GraphEdge list to Cypher queries."""
    queries: list[dict[str, Any]] = []
    
    for edge in edges:
        edge_type = edge.edge_type  # 이미 Framework 스키마에 맞춘 문자열
        props = edge.properties.copy()
        
        query = (
            "MATCH (__cy_src__) WHERE id(__cy_src__) = $source_id OR elementId(__cy_src__) = $source_id\n"
            "MATCH (__cy_tgt__) WHERE id(__cy_tgt__) = $target_id OR elementId(__cy_tgt__) = $target_id\n"
            f"MERGE (__cy_src__)-[__cy_r__:{edge_type}]->(__cy_tgt__)\n"
            "SET __cy_r__ += $props\n"
            "RETURN __cy_r__"
        )
        
        queries.append({
            "query": query,
            "params": {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "props": props,
            },
        })
    
    return queries

