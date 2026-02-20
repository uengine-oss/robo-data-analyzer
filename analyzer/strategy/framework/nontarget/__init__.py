"""Nontarget 파일 처리 모듈

deep-agents-modernizer 방식으로 XML, YAML, Properties 등 비타겟 파일을 처리합니다.
Java/Kotlin 파일은 ANTLR로 처리되므로 이 모듈에서는 제외됩니다.
"""

import json
from pathlib import Path
from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.framework.nontarget.data_models import (
    FileInfo,
    FormatRegistry,
    ProjectStructure,
    Chunk,
    Relationship,
    GraphNode,
    GraphEdge,
)
from analyzer.strategy.framework.nontarget.phase1_5_reconnaissance import run_phase1_5_reconnaissance
from analyzer.strategy.framework.nontarget.phase2_5_schema_discovery import run_phase2_5_schema_discovery
from analyzer.strategy.framework.nontarget.phase3_chunking import run_phase3_chunking
from analyzer.strategy.framework.nontarget.phase4_relationships import run_phase4_relationships
from analyzer.strategy.framework.nontarget.phase5_ontology import run_phase5_ontology
from analyzer.strategy.framework.nontarget.phase5_5_enrichment import run_phase5_5_enrichment
from analyzer.strategy.framework.nontarget.phase6_validation import run_phase6_validation


async def run_nontarget_phase(
    analyzer: Any,
    file_names: list[tuple[str, str]],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Nontarget 파일 처리 파이프라인 실행
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        file_names: nontarget 파일 목록 [(directory, file_name), ...]
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    
    Yields:
        NDJSON 스트리밍 데이터
    """
    if not file_names:
        return
    
    # 프로젝트 루트 경로 결정 (orchestrator.dirs["analysis"]의 부모 또는 src)
    analysis_dir = orchestrator.dirs.get("analysis", "")
    src_dir = orchestrator.dirs.get("src", "")
    
    # 프로젝트 루트는 analysis 또는 src의 부모 디렉토리
    if analysis_dir:
        project_root = Path(analysis_dir).parent
    elif src_dir:
        project_root = Path(src_dir).parent
    else:
        # Fallback: 현재 작업 디렉토리
        project_root = Path(".")
    
    # file_names에서 FileInfo 생성
    from analyzer.strategy.framework.nontarget.data_models import FileCategory
    from analyzer.strategy.framework.nontarget.file_identifier import identify_file_type
    
    project_files: list[FileInfo] = []
    for directory, file_name in file_names:
        # 파일 경로 구성
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
    
    # Phase 결과 저장용 변수
    project_structure = ProjectStructure(
        root_path=project_root,
        files=project_files,
        total_files=len(project_files),
    )
    
    # Phase 1.5: 정찰 (이미 file_names에서 FileInfo 생성했으므로 스킵 가능하지만, 메타데이터 추출을 위해 실행)
    phase1_5_result = None
    async for chunk in run_phase1_5_reconnaissance(
        root_path=project_root,
        exclude_patterns=None,
        max_file_size_mb=10.0,
    ):
        yield chunk
        # Phase 결과 수집
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 1.5:
                phase1_5_result = payload.get("data", {})
                if phase1_5_result.get("project_structure"):
                    project_structure = phase1_5_result["project_structure"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    
    # Phase 2.5: 스키마 발견
    format_registry = FormatRegistry()
    generated_codes: dict[str, Any] = {}
    async for chunk in run_phase2_5_schema_discovery(
        project=project_structure,
        api_key=orchestrator.api_key,
        schema_sample_count=5,
        use_code_generation=False,  # TODO: CodeGenerator 통합 후 활성화
    ):
        yield chunk
        # Phase 결과 수집
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 2.5:
                phase2_5_result = payload.get("data", {})
                if phase2_5_result.get("format_registry"):
                    format_registry = phase2_5_result["format_registry"]
                if phase2_5_result.get("generated_codes"):
                    generated_codes = phase2_5_result["generated_codes"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    
    # Phase 3: 청킹
    chunks: list[Chunk] = []
    async for chunk in run_phase3_chunking(
        project=project_structure,
        format_registry=format_registry,
        generated_codes=generated_codes if generated_codes else None,
    ):
        yield chunk
        # Phase 결과 수집
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 3:
                phase3_result = payload.get("data", {})
                if phase3_result.get("chunks"):
                    chunks = phase3_result["chunks"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    
    # Phase 4: 관계 발견
    relationships: list[Relationship] = []
    class_index: dict[str, Any] = {}
    bean_index: dict[str, Any] = {}
    sql_index: dict[str, Any] = {}
    async for chunk in run_phase4_relationships(
        project=project_structure,
        chunks=chunks,
        generated_codes=generated_codes if generated_codes else None,
    ):
        yield chunk
        # Phase 결과 수집
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 4:
                phase4_result = payload.get("data", {})
                if phase4_result.get("relationships"):
                    relationships = phase4_result["relationships"]
                if phase4_result.get("class_index"):
                    class_index = phase4_result["class_index"]
                if phase4_result.get("bean_index"):
                    bean_index = phase4_result["bean_index"]
                if phase4_result.get("sql_index"):
                    sql_index = phase4_result["sql_index"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    
    # Phase 5: 온톨로지 구축
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    async for chunk in run_phase5_ontology(
        project=project_structure,
        chunks=chunks,
        relationships=relationships,
        client=client,
        class_index=class_index,
        bean_index=bean_index,
        sql_index=sql_index,
    ):
        yield chunk
        # Phase 결과 수집
        try:
            payload = json.loads(chunk.decode("utf-8"))
            if payload.get("type") == "phase_result" and payload.get("phase") == 5:
                phase5_result = payload.get("data", {})
                if phase5_result.get("nodes"):
                    nodes = phase5_result["nodes"]
                if phase5_result.get("edges"):
                    edges = phase5_result["edges"]
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
    
    # Phase 5.5: LLM 보강 (선택적)
    # TODO: CustomLLMClient 초기화에 필요한 파라미터 확인 필요
    # from client.llm_client import CustomLLMClient
    # llm_client = CustomLLMClient(
    #     api_key=orchestrator.api_key,
    #     model=settings.llm.model,
    #     base_url=settings.llm.base_url,
    # )
    # async for chunk in run_phase5_5_enrichment(
    #     project=project_structure,
    #     chunks=chunks,
    #     nodes=nodes,
    #     edges=edges,
    #     client=client,
    #     llm_client=llm_client,
    #     class_index=class_index,
    #     sql_index=sql_index,
    # ):
    #     yield chunk
    
    # Phase 5.5: LLM 보강 (선택적) - 현재 주석 처리
    # Phase 5.5 결과 수집은 나중에 활성화 시 추가
    
    # Phase 6: 검증
    async for chunk in run_phase6_validation(
        project=project_structure,
        nodes=nodes,
        edges=edges,
        relationships=relationships,
        client=client,
    ):
        yield chunk
        # Phase 결과 수집 (검증 리포트는 로깅용이므로 수집 불필요)
        # try:
        #     payload = json.loads(chunk.decode("utf-8"))
        #     if payload.get("type") == "phase_result" and payload.get("phase") == 6:
        #         phase6_result = payload.get("data", {})
        #         # validation_report는 로깅용이므로 수집 불필요
        # except (json.JSONDecodeError, KeyError, AttributeError):
        #     pass
