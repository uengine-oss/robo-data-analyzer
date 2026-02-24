"""Artifacts 파일 처리 모듈

deep-agents-modernizer 방식으로 XML, YAML, Properties 등 비타겟 파일을 처리합니다.
Java/Kotlin 파일은 ANTLR로 처리되므로 이 모듈에서는 제외됩니다.
"""

import json
from pathlib import Path
from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.framework.artifacts.data_models import (
    Chunk,
    FileInfo,
    FormatRegistry,
    GraphEdge,
    GraphNode,
    ProjectStructure,
    Relationship,
)
from analyzer.strategy.framework.artifacts.reconnaissance import run_reconnaissance
from analyzer.strategy.framework.artifacts.schema_discovery import run_schema_discovery
from analyzer.strategy.framework.artifacts.chunking import run_chunking
from analyzer.strategy.framework.artifacts.relationships import run_relationships
from analyzer.strategy.framework.artifacts.ontology import run_ontology
from analyzer.strategy.framework.artifacts.enrichment import run_enrichment
from analyzer.strategy.framework.artifacts.validation import run_validation


async def run_artifacts_phase(
    analyzer: Any,
    file_names: list[tuple[str, str]],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
    ast_context: list[Any] | None = None,
) -> AsyncGenerator[bytes, None]:
    """Artifacts 파일 처리 파이프라인 실행

    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        file_names: artifacts 파일 목록 [(directory, file_name), ...]
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
        ast_context: Java AST 노드 리스트 (JPA 등 persistence 추출용)

    Yields:
        NDJSON 스트리밍 데이터
    """
    if not file_names:
        return

    analysis_dir = orchestrator.dirs.get("analysis", "")
    src_dir = orchestrator.dirs.get("src", "")

    if analysis_dir:
        project_root = Path(analysis_dir).parent
    elif src_dir:
        project_root = Path(src_dir).parent
    else:
        project_root = Path(".")

    from analyzer.strategy.framework.artifacts.data_models import FileCategory
    from analyzer.strategy.framework.artifacts.file_identifier import identify_file_type

    project_files: list[FileInfo] = []
    for directory, file_name in file_names:
        if directory:
            file_path = project_root / directory / file_name
        else:
            file_path = project_root / file_name

        if file_path.exists():
            category, detected_type, mime_type = identify_file_type(file_path)
            project_files.append(FileInfo(
                path=file_path,
                relative_path=f"{directory}/{file_name}" if directory else file_name,
                extension=file_path.suffix.lower(),
                size=file_path.stat().st_size,
                category=category,
                detected_type=detected_type,
                mime_type=mime_type,
            ))

    if not project_files:
        return

    project_structure = ProjectStructure(
        root_path=project_root,
        files=project_files,
        total_files=len(project_files),
    )

    phase1_5_result = None
    async for chunk in run_reconnaissance(
        root_path=project_root,
        exclude_patterns=None,
        max_file_size_mb=10.0,
    ):
        yield chunk
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 1.5:
                phase1_5_result = payload.get("data", {})
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

    format_registry = FormatRegistry()
    generated_codes: dict[str, Any] = {}
    async for chunk in run_schema_discovery(
        project=project_structure,
        api_key=orchestrator.api_key,
        schema_sample_count=5,
        use_code_generation=False,
        registry=format_registry,
        generated_codes=generated_codes,
    ):
        # Schema discovery 단계의 스트리밍 출력은 그대로 전달만 하고,
        # 실제 FormatRegistry/코드 아티팩트는 참조로 전달된 객체에 누적된다.
        yield chunk

    chunks: list[Chunk] = []
    async for chunk in run_chunking(
        project=project_structure,
        format_registry=format_registry,
        generated_codes=generated_codes if generated_codes else None,
        chunks_out=chunks,
    ):
        # Chunk 리스트는 chunks_out 인자를 통해 직접 전달받으므로
        # phase_result 데이터는 파싱할 필요가 없다.
        yield chunk

    relationships: list[Relationship] = []
    class_index: dict[str, Any] = {}
    bean_index: dict[str, Any] = {}
    sql_index: dict[str, Any] = {}
    table_index: dict[str, Any] = {}
    async for chunk in run_relationships(
        project=project_structure,
        chunks=chunks,
        generated_codes=generated_codes if generated_codes else None,
        ast_context=ast_context,
        relationships_out=relationships,
        class_index_out=class_index,
        bean_index_out=bean_index,
        sql_index_out=sql_index,
        table_index_out=table_index,
    ):
        # 관계 및 인덱스 역시 아규먼트로 전달된 컨테이너에 누적된다.
        yield chunk

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    async for chunk in run_ontology(
        project=project_structure,
        chunks=chunks,
        relationships=relationships,
        client=client,
        class_index=class_index,
        bean_index=bean_index,
        sql_index=sql_index,
        ast_context=ast_context,
        nodes_out=nodes,
        edges_out=edges,
    ):
        # Neo4j 로드에 사용된 노드/관계 리스트는 nodes_out/edges_out 에 누적된다.
        yield chunk

    async for chunk in run_validation(
        project=project_structure,
        nodes=nodes,
        edges=edges,
        relationships=relationships,
        client=client,
    ):
        yield chunk
