"""Validation - Verify ontology completeness, connectivity, consistency.

Neo4jClient 통합 및 Framework 스키마에 맞춤.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.artifacts.data_models import (
    GraphEdge,
    GraphNode,
    ProjectStructure,
    Relationship,
    ValidationReport,
)


async def run_validation(
    project: ProjectStructure,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    relationships: list[Relationship],
    client: Neo4jClient,
) -> AsyncGenerator[bytes, None]:
    """검증 및 정제
    
    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        nodes: Phase 5에서 생성된 노드
        edges: Phase 5에서 생성된 관계
        relationships: Phase 4에서 발견된 관계
        client: Neo4j 클라이언트
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 6 시작 이벤트
    yield emit_phase_event(6, "비타겟 검증", "started", 0, {"canPause": True})
    
    try:
        report = ValidationReport()
        report.total_nodes = len(nodes)
        report.total_edges = len(edges)
        
        # Completeness check
        log_process("ANALYZE", "PHASE_6", "완전성 검사 중...")
        _check_completeness(project, nodes, report)
        log_process(
            "ANALYZE", "PHASE_6",
            f"완전성: {report.completeness_score * 100:.1f}% "
            f"({int(report.completeness_score * len(project.files)) if project.files else 0}/{len(project.files)} 파일 커버)"
        )
        yield emit_message(f"   ✓ 완전성: {report.completeness_score * 100:.1f}%")
        
        # Connectivity check
        log_process("ANALYZE", "PHASE_6", "연결성 검사 중...")
        _check_connectivity(nodes, edges, report)
        log_process(
            "ANALYZE", "PHASE_6",
            f"연결성: {report.connectivity_score * 100:.1f}% ({len(report.orphan_nodes)}개 고아 노드)"
        )
        yield emit_message(f"   ✓ 연결성: {report.connectivity_score * 100:.1f}% ({len(report.orphan_nodes)}개 고아 노드)")
        
        # Consistency check
        log_process("ANALYZE", "PHASE_6", "일관성 검사 중...")
        _check_consistency(nodes, edges, report)
        log_process(
            "ANALYZE", "PHASE_6",
            f"일관성: {report.consistency_score * 100:.1f}% ({len(report.broken_references)}개 깨진 참조)"
        )
        yield emit_message(f"   ✓ 일관성: {report.consistency_score * 100:.1f}% ({len(report.broken_references)}개 깨진 참조)")
        
        # Confidence filtering
        log_process("ANALYZE", "PHASE_6", "낮은 신뢰도 관계 필터링 중...")
        _filter_low_confidence(relationships, report, threshold=0.5)
        if report.low_confidence_relations:
            log_process("ANALYZE", "PHASE_6", f"{len(report.low_confidence_relations)}개 낮은 신뢰도 관계 발견")
            yield emit_message(f"   ⚠️ 낮은 신뢰도 관계 {len(report.low_confidence_relations)}개 발견")
        
        # Log issues
        if report.issues:
            log_process("ANALYZE", "PHASE_6", "검증 이슈 발견:")
            for issue in report.issues:
                log_process("ANALYZE", "PHASE_6", f"  - {issue}")
                yield emit_message(f"   ⚠️ {issue}")
        else:
            log_process("ANALYZE", "PHASE_6", "검증 이슈 없음")
            yield emit_message("   ✓ 검증 이슈 없음")
        
        log_process(
            "ANALYZE", "PHASE_6",
            f"요약: 완전성={report.completeness_score * 100:.1f}%, "
            f"연결성={report.connectivity_score * 100:.1f}%, "
            f"일관성={report.consistency_score * 100:.1f}%"
        )
        
        # Phase 6 완료 이벤트
        yield emit_phase_event(6, "비타겟 검증", "completed", 100, {
            "completeness_score": report.completeness_score,
            "connectivity_score": report.connectivity_score,
            "consistency_score": report.consistency_score,
            "orphan_nodes": len(report.orphan_nodes),
            "broken_references": len(report.broken_references),
        })
        
        # Phase 결과 반환
        yield emit_phase_result(6, {
            "validation_report": report,
        })
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 6 실패: {e}", exc_info=True)
        yield emit_phase_event(6, "비타겟 검증", "failed", 0, {"error": str(e)})
        raise


def _check_completeness(
    project: ProjectStructure,
    nodes: list[GraphNode],
    report: ValidationReport,
) -> None:
    """Check that all significant files are represented in the graph."""
    # Count files that should have nodes (excluding binary/unknown)
    significant_files = [
        f for f in project.files
        if f.category.value not in ("binary", "unknown")
    ]
    total_significant = len(significant_files)
    
    # Count files that have corresponding nodes
    node_file_paths: set[str] = set()
    for node in nodes:
        fp = node.properties.get("file_name", node.properties.get("filePath", ""))
        if fp:
            node_file_paths.add(fp)
        # Also check directory + file_name
        directory = node.properties.get("directory", "")
        if directory and fp:
            node_file_paths.add(f"{directory}/{fp}")
    
    covered_files = sum(
        1 for f in significant_files if f.relative_path in node_file_paths or f.path.name in node_file_paths
    )
    
    if total_significant > 0:
        report.completeness_score = covered_files / total_significant
    else:
        report.completeness_score = 1.0
    
    # Report uncovered files
    uncovered = [
        f.relative_path for f in significant_files
        if f.relative_path not in node_file_paths and f.path.name not in node_file_paths
    ]
    if uncovered:
        report.issues.append(
            f"커버되지 않은 파일 ({len(uncovered)}개): {', '.join(uncovered[:10])}"
            + ("..." if len(uncovered) > 10 else "")
        )


def _check_connectivity(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    report: ValidationReport,
) -> None:
    """Check graph connectivity and identify orphan nodes."""
    connected_ids: set[str] = set()
    for edge in edges:
        connected_ids.add(edge.source_id)
        connected_ids.add(edge.target_id)
    
    all_ids = {n.node_id for n in nodes}
    orphan_ids = all_ids - connected_ids
    report.orphan_nodes = list(orphan_ids)
    
    if nodes:
        orphan_ratio = len(orphan_ids) / len(nodes)
        report.connectivity_score = 1.0 - orphan_ratio
    else:
        report.connectivity_score = 1.0
    
    if orphan_ids:
        # Get names for logging
        orphan_names = []
        for node in nodes:
            if node.node_id in orphan_ids:
                name = node.properties.get("name", node.properties.get("file_name", node.node_id))
                orphan_names.append(f"{node.label}:{name}")
        
        report.issues.append(
            f"고아 노드 ({len(orphan_ids)}개): {', '.join(orphan_names[:10])}"
            + ("..." if len(orphan_names) > 10 else "")
        )


def _check_consistency(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    report: ValidationReport,
) -> None:
    """Check referential integrity of the graph."""
    node_ids = {n.node_id for n in nodes}
    broken = []
    
    for edge in edges:
        if edge.source_id not in node_ids:
            broken.append({
                "type": "missing_source",
                "source": edge.source_id,
                "target": edge.target_id,
                "edge_type": edge.edge_type,
            })
        if edge.target_id not in node_ids:
            broken.append({
                "type": "missing_target",
                "source": edge.source_id,
                "target": edge.target_id,
                "edge_type": edge.edge_type,
            })
    
    report.broken_references = broken
    
    if edges:
        broken_ratio = len(broken) / len(edges)
        report.consistency_score = 1.0 - broken_ratio
    else:
        report.consistency_score = 1.0
    
    if broken:
        broken_refs = [f"{b['type']}: {b['source']} -> {b['target']}" for b in broken[:5]]
        broken_msg = f"깨진 참조 ({len(broken)}개): {', '.join(broken_refs)}"
        if len(broken) > 5:
            broken_msg += "..."
        report.issues.append(broken_msg)


def _filter_low_confidence(
    relationships: list[Relationship],
    report: ValidationReport,
    threshold: float = 0.5,
) -> None:
    """Filter relationships with low confidence."""
    low_confidence = [r for r in relationships if r.confidence < threshold]
    report.low_confidence_relations = low_confidence

